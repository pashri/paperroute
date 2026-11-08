"""Timetable repository over the built SQLite file."""

# pylint: disable=too-many-lines

import math
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import batched
from pathlib import Path
from typing import Final, cast

from paperroute.city import SydneyFeed
from paperroute.core.strip_times import (
    board_departures,
    filtered,
    strip_departures,
)
from paperroute_device.adapters.sqlite_ro import (
    Identity,
    ReadOnlyDatabase,
    file_identity,
)
from paperroute_transit.adapters.alert_rows import (
    LineMemory,
    alert_places,
    read_trainlink,
)
from paperroute_transit.adapters.calendar_rows import (
    SERVICE_DAY_LENGTH,
    read_holidays,
    service_span,
)
from paperroute_transit.adapters.carry_on_rows import (
    CARRY_ONS,
    carry_on_departure,
    first_to_arrive,
)
from paperroute_transit.adapters.departure_rows import (
    DEPARTURES,
    WHOLE_STOP,
    to_departure,
)
from paperroute_transit.adapters.pattern_rows import (
    JOINED_TRIPS,
    LABELS_BY_ALIAS,
    LABELS_BY_TRIP,
    PATTERN_STOPS,
    RUNNING_DAYS,
    pattern_of,
)
from paperroute_transit.core.alert_model import AlertPlaces
from paperroute_transit.core.city import City
from paperroute_transit.core.geo import (
    MetreBox,
    Point,
    Projection,
    Shape,
    to_lonlat,
    to_metres,
    unpack_shape,
)
from paperroute_transit.core.model import (
    HIDDEN_HEADSIGNS,
    Departure,
    Pin,
    ServiceSpan,
    StopPlace,
    service_epoch,
    service_seconds,
)
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.reach import StripKey

DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/timetable.db')
CHUNK: Final[int] = 500
LABEL_LIMIT: Final[int] = 20_000
NO_SHAPE: Final[str] = 'NULL'
MATCH_RADIUS: Final[float] = 100.0  # metres
SCHEMA_CHECK: Final[str] = """
SELECT
  (SELECT COUNT(*) FROM pragma_table_info('trips')
   WHERE name = 'shape_id'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'shapes'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'trip_next'),
  (SELECT COUNT(*) FROM pragma_table_info('trip_next')
   WHERE name = 'next_trip'),
  (SELECT COUNT(*) FROM pragma_table_info('trips')
   WHERE name = 'service'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'trip_joins')
"""
NEXT_COLUMNS: Final[str] = 'n.route, n.headsign, n.next_trip'
OLD_NEXT_COLUMNS: Final[str] = 'n.route, n.headsign, NULL'
SERVICE_ONLY: Final[str] = 'AND t.service = 1'
NEXT_JOIN: Final[str] = 'LEFT JOIN trip_next n ON n.trip_id = t.trip_id'
NO_NEXT: Final[str] = 'NULL, NULL, NULL'
NAMED_NEAR: Final[str] = """
SELECT stop_id, lon, lat FROM stops
WHERE name = :name AND lon BETWEEN :west AND :east
  AND lat BETWEEN :south AND :north
"""
TRIP_COLUMNS: Final[str] = """
t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  {shape}
"""
BY_TRIP: Final[str] = (
    'SELECT t.trip_id, ' + TRIP_COLUMNS + """
FROM trips t JOIN routes r ON r.route_id = t.route_id
WHERE t.trip_id IN ({marks})
"""
)
BY_ALIAS: Final[str] = (
    'SELECT a.alias_id, ' + TRIP_COLUMNS + """
FROM trip_aliases a JOIN trips t ON t.trip_id = a.trip_id
JOIN routes r ON r.route_id = t.route_id
WHERE a.alias_id IN ({marks})
ORDER BY a.alias_id, t.trip_id
"""
)
SHAPES_BY_ID: Final[str] = (
    'SELECT shape_id, points FROM shapes WHERE shape_id IN ({marks})'
)
MODES_NEAR: Final[str] = """
SELECT DISTINCT mode FROM shapes
WHERE west <= :east AND east >= :west
  AND south <= :north AND north >= :south
"""


@dataclass(frozen=True)
class Features:
    """
    Which optional parts of the schema a timetable file has.

    Attributes
    ----------
    shape_column : bool
        Whether trips has a shape_id column.
    shapes : bool
        Whether the shapes table exists.
    carry_ons : bool
        Whether the trip_next table exists.
    next_trips : bool
        Whether trip_next has the next_trip column.
    service : bool
        Whether trips has the service column.
    joins : bool
        Whether the trip_joins table exists.
    """

    shape_column: bool = False
    shapes: bool = False
    carry_ons: bool = False
    next_trips: bool = False
    service: bool = False
    joins: bool = False

    @property
    def next_columns(self) -> str:
        """
        The carry-on columns a departure query selects.

        Returns
        -------
        str
            Next route, headsign and trip, NULL where the file lacks them.
        """
        if not self.carry_ons:
            return NO_NEXT
        return NEXT_COLUMNS if self.next_trips else OLD_NEXT_COLUMNS


# pylint: disable-next=too-many-public-methods
class SqliteTimetableRepository:
    """
    Read-only queries against the timetable file.

    Parameters
    ----------
    city : City
        The city: its time zone (the timetable's service days are in it),
        its projection (stop points are placed in it) and its route
        classifier.
    path : Path | None
        Timetable file; defaults to the device location.
    connection : sqlite3.Connection | None
        An open connection to use instead of opening path. close() closes
        it too. A repository given a connection never reopens.

    Notes
    -----
    When built with a path, the file's identity is checked before each
    query and the connection is reopened if the file was replaced. A lock
    serialises queries and the reopen, so a connection is never closed
    while another thread is querying it.
    """

    def __init__(
        self,
        *,
        city: City,
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.zone = city.zone
        self.projection = city.projection
        self.classify = city.classify
        self.db = ReadOnlyDatabase(
            path=path or DEFAULT_PATH,
            connection=connection,
            probe='SELECT 1 FROM stops LIMIT 1',
            label='timetable',
            allow_missing=True,
        )
        self.schema: tuple[Identity | None, Features] | None = None
        self.labels: dict[str, str] = {}
        self.labels_id: Identity | None = None
        self.lines = LineMemory()

    @property
    def identity(self) -> tuple[int, int] | None:
        """
        The identity of the file the open connection reads.

        Returns
        -------
        tuple[int, int] | None
            (st_dev, st_ino), or None when there is no file.
        """
        return self.db.identity

    def fetch(
        self, *, sql: str, params: tuple[object, ...] | dict[str, object]
    ) -> list[tuple[object, ...]]:
        """
        Run a query against the current file.

        Parameters
        ----------
        sql : str
            Query.
        params : tuple[object, ...] | dict[str, object]
            Query parameters.

        Returns
        -------
        list[tuple[object, ...]]
            All rows.
        """
        return self.db.fetch(sql=sql, params=params)

    def fetch_in(
        self,
        *,
        sql: str,
        ids: Collection[str],
        lead: tuple[object, ...] = (),
        shape: str = NO_SHAPE,
    ) -> list[tuple[object, ...]]:
        """
        Run a query with an IN list, in chunks.

        Parameters
        ----------
        sql : str
            Query with a ``{marks}`` placeholder for the IN list and, when
            it selects a shape, a ``{shape}`` placeholder.
        ids : Collection[str]
            Values for the IN list.
        lead : tuple[object, ...]
            Parameters that come before the IN list.
        shape : str
            Expression to select in place of ``{shape}``.

        Returns
        -------
        list[tuple[object, ...]]
            Rows of every chunk, with each chunk's IDs in order.
        """
        rows: list[tuple[object, ...]] = []
        for chunk in batched(sorted(set(ids)), CHUNK):
            query = sql.format(marks=','.join('?' * len(chunk)), shape=shape)
            rows.extend(self.fetch(sql=query, params=(*lead, *chunk)))
        return rows

    def features(self) -> Features:
        """
        Say which optional tables the file has, after any replacement.

        The answer is kept while the file is unchanged on disk. When it
        may have changed, a query first lets the database reopen it, and
        the answer is worked out again for the file then open.

        Returns
        -------
        Features
            What the file has; nothing for a file built before shapes.
        """
        path = self.db.path
        known = self.schema
        if known is not None and (
            path is None or file_identity(path=path) == known[0]
        ):
            return known[1]
        self.fetch(sql='SELECT 1', params=())
        if known is None or known[0] != self.identity:
            rows = self.fetch(sql=SCHEMA_CHECK, params=())
            found = rows[0] if rows else (0, 0, 0, 0, 0, 0)
            known = (
                self.identity,
                Features(
                    shape_column=bool(found[0]),
                    shapes=bool(found[1]),
                    carry_ons=bool(found[2]),
                    next_trips=bool(found[3]),
                    service=bool(found[4]),
                    joins=bool(found[5]),
                ),
            )
            self.schema = known
        return known[1]

    def shape_column(self) -> str:
        """
        The expression that selects a trip's shape ID.

        Returns
        -------
        str
            ``t.shape_id``, or NULL for a file without the column.
        """
        return 't.shape_id' if self.features().shape_column else NO_SHAPE

    def service_filter(self) -> str:
        """
        The clause that keeps out-of-service trips off a board.

        Returns
        -------
        str
            ``AND t.service = 1``, or nothing for a file without the
            column, which has no such trips.
        """
        return SERVICE_ONLY if self.features().service else ''

    def version(self) -> str | None:
        """
        Say which build the file is.

        Returns
        -------
        str | None
            The build time, or None when the file doesn't record one.
        """
        rows = self.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('built_at',),
        )
        return str(rows[0][0]) if rows else None

    def schema_version(self) -> str | None:
        """
        Say which schema the file is built to.

        Returns
        -------
        str | None
            The schema version, or None when the file doesn't record one.
        """
        rows = self.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('schema_version',),
        )
        return str(rows[0][0]) if rows else None

    def origin(self) -> str | None:
        """
        Say which projection origin the file's metres are from.

        Returns
        -------
        str | None
            The origin text, or None when the file doesn't record one.
        """
        rows = self.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('origin',),
        )
        return str(rows[0][0]) if rows else None

    def holidays(self) -> frozenset[date]:
        """
        The public holidays the file records.

        Returns
        -------
        frozenset[date]
            The holidays; none for a file built without them.
        """
        return read_holidays(
            fetch=lambda sql: self.fetch(sql=sql, params=()),
        )

    def service_span(self, *, pin: Pin, day: date) -> ServiceSpan | None:
        """
        When a pin's trips run on a service day.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        day : date
            Service day.

        Returns
        -------
        ServiceSpan | None
            The span of the trips `departures` lists that day, up to
            `SERVICE_DAY_LENGTH` after its start; None when there are
            none.
        """
        start = service_epoch(day=day, zone=self.zone)
        found = self.departures(
            pin=pin, start=start, end=start + SERVICE_DAY_LENGTH,
        )
        return service_span(
            departures=found,
            day=day,
            fetch_in=lambda sql, ids: self.fetch_in(sql=sql, ids=ids),
            zone=self.zone,
        )

    def stop(self, *, stop_id: str) -> StopPlace:
        """
        A stop or station with its position.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        StopPlace
            Name and point; the point is None without coordinates.

        Raises
        ------
        KeyError
            When the stop isn't in the timetable.
        """
        rows = self.fetch(
            sql='SELECT name, lon, lat FROM stops WHERE stop_id = ?',
            params=(stop_id,),
        )
        if not rows:
            raise KeyError(stop_id)
        name, lon, lat = rows[0]
        return StopPlace(
            stop_id=stop_id,
            name=str(name),
            point=to_point(lon=lon, lat=lat, projection=self.projection),
        )

    def match_stop(self, *, place: StopPlace) -> StopPlace | None:
        """
        Find a place again in the current timetable.

        Parameters
        ----------
        place : StopPlace
            A place from an earlier timetable.

        Returns
        -------
        StopPlace | None
            The stop with the same ID; else the nearest stop of the same
            name within MATCH_RADIUS of the place's point; else None. A
            place without a point matches by ID only.
        """
        try:
            return self.stop(stop_id=place.stop_id)
        except KeyError:
            if place.point is None:
                return None
            return self.nearest_named(name=place.name, point=place.point)

    def nearest_named(self, *, name: str, point: Point) -> StopPlace | None:
        """
        Find the nearest stop of a name within MATCH_RADIUS of a point.

        Parameters
        ----------
        name : str
            Exact stop name.
        point : Point
            Metres from the projection origin.

        Returns
        -------
        StopPlace | None
            The nearest such stop, the lowest ID on a tie, or None.
        """
        west, south = to_lonlat(
            point=(point[0] - MATCH_RADIUS, point[1] - MATCH_RADIUS),
            projection=self.projection,
        )
        east, north = to_lonlat(
            point=(point[0] + MATCH_RADIUS, point[1] + MATCH_RADIUS),
            projection=self.projection,
        )
        rows = self.fetch(
            sql=NAMED_NEAR,
            params={
                'name': name,
                'west': west,
                'east': east,
                'south': south,
                'north': north,
            },
        )
        near = sorted(
            (math.dist(found, point), str(stop_id), found)
            for stop_id, lon, lat in rows
            if (found := to_point(
                lon=lon,
                lat=lat,
                projection=self.projection,
            )) is not None
        )
        if not near or near[0][0] > MATCH_RADIUS:
            return None
        return StopPlace(stop_id=near[0][1], name=name, point=near[0][2])

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Route shapes by ID.

        Parameters
        ----------
        shape_ids : Collection[str]
            Namespaced shape IDs.

        Returns
        -------
        dict[str, Shape]
            The shapes found; unknown IDs are absent, and so is everything
            for a file built before shapes.
        """
        if not self.features().shapes:
            return {}
        rows = self.fetch_in(sql=SHAPES_BY_ID, ids=shape_ids)
        return {
            str(shape_id): unpack_shape(
                shape_id=str(shape_id),
                blob=cast(bytes, blob),
            )
            for shape_id, blob in rows
        }

    def modes_near(self, *, box: MetreBox) -> frozenset[SydneyFeed]:
        """
        List the modes of the shapes that overlap an area.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north in metres.

        Returns
        -------
        frozenset[SydneyFeed]
            The modes; empty for a file built before shapes.
        """
        if not self.features().shapes:
            return frozenset()
        west, south, east, north = box
        rows = self.fetch(
            sql=MODES_NEAR,
            params={
                'west': west,
                'south': south,
                'east': east,
                'north': north,
            },
        )
        return frozenset(SydneyFeed(str(row[0])) for row in rows)

    def resolve_trips(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, list[tuple[object, ...]]]:
        """
        Look up the trips behind requested IDs.

        A requested ID can be a trip, an alias of other trips, or both,
        when a bundle trip was only partly replaced by a feed trip.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, list[tuple[object, ...]]]
            Requested ID to its candidate rows (requested ID, trip ID,
            route, route type, agency, shape ID). The trip with that ID
            comes first, then aliased trips in trip ID order.
        """
        shape = self.shape_column()
        found: dict[str, list[tuple[object, ...]]] = {}
        for sql in (BY_TRIP, BY_ALIAS):
            for row in self.fetch_in(sql=sql, ids=trip_ids, shape=shape):
                found.setdefault(str(row[0]), []).append(row)
        return found

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Stops, shape and running dates of trips.

        An alias can stand for several trips. Its dates are all the dates
        any of them runs, and its stops and shape come from the first one
        that runs on those dates.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.
        around : date
            Service date; the day before is included as well.

        Returns
        -------
        dict[str, TripPattern]
            Patterns by requested ID; unknown trips are absent.
        """
        found = self.resolve_trips(trip_ids=trip_ids)
        real = {str(row[1]) for rows in found.values() for row in rows}
        days = self.running_days(trip_ids=real, around=around)
        calls = self.call_stops(trip_ids=real)
        nexts = self.joined_trips(trip_ids=real)
        return {
            requested: pattern_of(
                requested=requested,
                rows=rows,
                days=days,
                calls=(calls, nexts),
                classify=self.classify,
            )
            for requested, rows in found.items()
        }

    def joined_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Find the trip each trip's vehicle runs on through as.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, str]
            Joined trip by trip ID, for joins onto passenger trips; empty
            for a file without joins.
        """
        features = self.features()
        if not features.joins:
            return {}
        sql = JOINED_TRIPS + (' AND n.service = 1' if features.service else '')
        return {
            str(trip): str(joined)
            for trip, joined in self.fetch_in(sql=sql, ids=trip_ids)
        }

    def running_days(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, list[date]]:
        """
        Find the dates trips run on for a service date and the day before.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.
        around : date
            Later of the two service dates.

        Returns
        -------
        dict[str, list[date]]
            Trip ID to its dates, earliest first.
        """
        before = around - timedelta(days=1)
        rows = self.fetch_in(
            sql=RUNNING_DAYS,
            ids=trip_ids,
            lead=(before.isoformat(), around.isoformat()),
        )
        days: dict[str, list[date]] = {}
        for trip, day in rows:
            days.setdefault(str(trip), []).append(
                date.fromisoformat(str(day))
            )
        return days

    def call_stops(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, list[PatternStop]]:
        """
        List the stops of trips in running order.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, list[PatternStop]]
            Trip ID to its calls.
        """
        calls: dict[str, list[PatternStop]] = {}
        rows = self.fetch_in(sql=PATTERN_STOPS, ids=trip_ids)
        for trip, arr, dep, lon, lat, station, name, passes in rows:
            calls.setdefault(str(trip), []).append(
                PatternStop(
                    arrives=int(str(arr)),
                    departs=int(str(dep)),
                    point=to_point(
                        lon=lon,
                        lat=lat,
                        projection=self.projection,
                    ),
                    station=str(station),
                    name=str(name),
                    passes=bool(passes),
                )
            )
        return calls

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Route short names by trip or alias.

        Answers, including that an ID is unknown, are kept until the file
        is replaced or the cache outgrows its limit, so repeat lookups
        don't query.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, str]
            Short name by ID; unknown IDs are absent.
        """
        self.labels_checked()
        missing = {trip for trip in trip_ids if trip not in self.labels}
        result = {
            trip: label
            for trip in trip_ids
            if (label := self.labels.get(trip))
        }
        found = self.look_up_labels(trip_ids=missing)
        if len(self.labels) > LABEL_LIMIT:
            self.labels.clear()
        self.labels.update({trip: found.get(trip, '') for trip in missing})
        return result | found

    def labels_checked(self) -> None:
        """Forget the cached labels when the file has been replaced."""
        self.fetch(sql='SELECT 1', params=())
        if self.identity != self.labels_id:
            self.labels.clear()
            self.labels_id = self.identity

    def look_up_labels(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, str]:
        """
        Query route short names by trip, then by alias.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, str]
            Short name by ID.
        """
        found = {
            str(trip): str(label)
            for trip, label in self.fetch_in(
                sql=LABELS_BY_TRIP, ids=trip_ids
            )
        }
        for alias, label in self.fetch_in(
            sql=LABELS_BY_ALIAS, ids=set(trip_ids) - found.keys()
        ):
            found.setdefault(str(alias), str(label))
        return found

    def alert_places(
        self, *, route_ids: Collection[str], stop_ids: Collection[str]
    ) -> AlertPlaces:
        """
        Look up the routes and stops an alert feed names.

        Parameters
        ----------
        route_ids : Collection[str]
            Route IDs from the feed.
        stop_ids : Collection[str]
            Stop IDs from the feed.

        Returns
        -------
        AlertPlaces
            Route short names, stop families and the metro line; unknown
            IDs are absent.
        """
        return alert_places(
            route_ids=route_ids,
            stop_ids=stop_ids,
            fetch=lambda sql: self.fetch(sql=sql, params=()),
            fetch_in=lambda sql, ids: self.fetch_in(sql=sql, ids=ids),
        )

    def leg_lines(
        self, *, ends: tuple[str, str], day: date,
    ) -> frozenset[str]:
        """
        List the routes with a trip from one stop to another on a day.

        Parameters
        ----------
        ends : tuple[str, str]
            From and To stop or station IDs.
        day : date
            Service date.

        Returns
        -------
        frozenset[str]
            Route IDs ridden between them, kept for the file and day.
        """
        params = dict[str, object](origin=ends[0], dest=ends[1], day=f'{day}')
        return self.lines.read(
            joins=self.features().joins,
            key=(self.identity, ends, day),
            fetch=lambda sql: self.fetch(sql=sql, params=params),
        )

    def serves_trainlink(self, *, stop_ids: Collection[str]) -> bool:
        """
        Say whether an NSW TrainLink trip calls at any of some stops.

        Parameters
        ----------
        stop_ids : Collection[str]
            Stop IDs.

        Returns
        -------
        bool
            True when one does.
        """
        return read_trainlink(
            stop_ids=stop_ids,
            fetch_in=lambda sql, ids: self.fetch_in(sql=sql, ids=ids),
        )

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Direct services leaving the pin's From between two moments.

        Service days from the day before start to end's day are searched, so
        trips running past midnight are found.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            In departure order, then trip ID. With a Destination, only
            the pin's kind and routes, as on its strip: a station's bus
            stands sit under it too. Without one, the departures of the
            pin's kind (see `board_departures`) from any platform of the
            From, each running to its last stop. A
            trip calling at the From several times in a row, such as a
            bus moving between two stands, leaves once, from its last call
            there that takes passengers on. The pin's route filter
            (`Pin.only`) is applied last.
        """
        destination = pin.destination
        first = start.astimezone(self.zone).date()
        last = end.astimezone(self.zone).date()
        days = {first - timedelta(days=1), first, last}
        found = [
            departure
            for day in sorted(days)
            for departure in self.on_day(
                ends=(
                    pin.origin.stop_id,
                    None if destination is None else destination.stop_id,
                ),
                day=day,
                start=start,
                end=end,
            )
        ]
        if destination is None:
            found = list(board_departures(departures=found, kind=pin.kind))
        else:
            found = list(strip_departures(
                departures=first_to_arrive(departures=found),
                key=StripKey(
                    station=pin.origin.stop_id,
                    kind=pin.kind,
                    routes=pin.routes,
                ),
            ))
        return sorted(
            filtered(departures=found, only=pin.only),
            key=lambda departure: (departure.departs, departure.trip_id),
        )

    def on_day(
        self,
        *,
        ends: tuple[str, str | None],
        day: date,
        start: datetime,
        end: datetime,
    ) -> list[Departure]:
        """
        List departures from one service day within the range.

        Parameters
        ----------
        ends : tuple[str, str | None]
            From and Destination stop or station IDs; no Destination for
            the whole stop.
        day : date
            Service date.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            Unsorted departures, leaving out empty-running trips. With
            a Destination, trips that reach it only by running on as
            another trip are added (see CARRY_ONS).
        """
        features = self.features()
        sql = DEPARTURES if ends[1] is not None else WHOLE_STOP
        params: dict[str, object] = {
            'day': day.isoformat(),
            'origin': ends[0],
            'dest': ends[1],
            'lo': service_seconds(day=day, moment=start, zone=self.zone),
            'hi': service_seconds(day=day, moment=end, zone=self.zone),
        }
        rows = self.fetch(
            sql=sql.format(
                shape=self.shape_column(),
                then=features.next_columns,
                next_join=NEXT_JOIN if features.carry_ons else '',
                service=self.service_filter(),
            ),
            params=params,
        )
        found = [
            to_departure(
                row=row, day=day, zone=self.zone, classify=self.classify,
            )
            for row in rows
        ]
        if ends[1] is not None and features.joins:
            found.extend(
                carry_on_departure(
                    row=row, day=day, zone=self.zone, classify=self.classify,
                )
                for row in self.fetch(
                    sql=CARRY_ONS.format(
                        shape=self.shape_column(),
                        service=self.service_filter(),
                    ),
                    params=params,
                )
            )
        return [
            departure
            for departure in found
            if departure.headsign not in HIDDEN_HEADSIGNS
        ]

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()


def to_point(
    *,
    lon: object,
    lat: object,
    projection: Projection,
) -> Point | None:
    """
    Project a stop's coordinates.

    Parameters
    ----------
    lon : object
        Longitude column.
    lat : object
        Latitude column.
    projection : Projection
        The projection to place the stop in.

    Returns
    -------
    Point | None
        Metres from the projection origin, or None when either is NULL.
    """
    if lon is None or lat is None:
        return None
    return to_metres(
        lon=float(str(lon)),
        lat=float(str(lat)),
        projection=projection,
    )
