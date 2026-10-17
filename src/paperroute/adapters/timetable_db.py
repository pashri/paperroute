"""Timetable repository over the built SQLite file."""

import sqlite3
from collections.abc import Collection
from datetime import date, datetime, timedelta
from itertools import batched
from pathlib import Path
from typing import Final, cast

from paperroute.adapters.sqlite_ro import Identity, ReadOnlyDatabase
from paperroute.core.geo import MetreBox, Point, Shape, to_metres, unpack_shape
from paperroute.core.model import (
    SYDNEY,
    Departure,
    Mode,
    Pin,
    StopPlace,
    mode_for,
    service_seconds,
    service_time,
)
from paperroute.core.patterns import PatternStop, TripPattern

DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/timetable.db')
CHUNK: Final[int] = 500
LABEL_LIMIT: Final[int] = 20_000
NO_SHAPE: Final[str] = 'NULL'
SCHEMA_CHECK: Final[str] = """
SELECT
  (SELECT COUNT(*) FROM pragma_table_info('trips')
   WHERE name = 'shape_id'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'shapes')
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
LABELS_BY_TRIP: Final[str] = """
SELECT t.trip_id, r.short_name FROM trips t
JOIN routes r ON r.route_id = t.route_id
WHERE t.trip_id IN ({marks})
"""
LABELS_BY_ALIAS: Final[str] = """
SELECT a.alias_id, r.short_name FROM trip_aliases a
JOIN trips t ON t.trip_id = a.trip_id
JOIN routes r ON r.route_id = t.route_id
WHERE a.alias_id IN ({marks})
ORDER BY a.alias_id, t.trip_id
"""
RUNNING_DAYS: Final[str] = """
SELECT t.trip_id, sv.date FROM trips t
JOIN services sv ON sv.service_id = t.service_id
WHERE sv.date IN (?, ?) AND t.trip_id IN ({marks})
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = sv.date)
ORDER BY t.trip_id, sv.date
"""
PATTERN_STOPS: Final[str] = """
SELECT st.trip_id, st.arr, st.dep, s.lon, s.lat FROM stop_times st
JOIN stops s ON s.stop_id = st.stop_id
WHERE st.trip_id IN ({marks})
ORDER BY st.trip_id, st.seq
"""
SHAPES_BY_ID: Final[str] = (
    'SELECT shape_id, points FROM shapes WHERE shape_id IN ({marks})'
)
MODES_NEAR: Final[str] = """
SELECT DISTINCT mode FROM shapes
WHERE west <= :east AND east >= :west
  AND south <= :north AND north >= :south
"""
DEPARTURES: Final[str] = """
SELECT t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  t.headsign, os.platform,
  o.seq, o.stop_id, MIN(d.seq), d.stop_id, o.dep, d.arr,
  (SELECT group_concat(a.alias_id, ' ') FROM trip_aliases a
     WHERE a.trip_id = t.trip_id),
  {shape}
FROM stop_times o
JOIN trips t ON t.trip_id = o.trip_id
JOIN services sv ON sv.service_id = t.service_id AND sv.date = :day
JOIN routes r ON r.route_id = t.route_id
JOIN stops os ON os.stop_id = o.stop_id
JOIN stop_times d ON d.trip_id = o.trip_id AND d.seq > o.seq
WHERE o.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :origin OR parent_station = :origin)
  AND d.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :dest OR parent_station = :dest)
  AND o.dep BETWEEN :lo AND :hi AND o.pickup <> 1 AND d.dropoff <> 1
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = :day)
GROUP BY o.trip_id, o.seq
"""


class SqliteTimetableRepository:
    """
    Read-only queries against the timetable file.

    Parameters
    ----------
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
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db = ReadOnlyDatabase(
            path=path or DEFAULT_PATH,
            connection=connection,
            probe='SELECT 1 FROM stops LIMIT 1',
            label='timetable',
        )
        self.schema: tuple[Identity | None, bool, bool] | None = None
        self.labels: dict[str, str] = {}
        self.labels_id: Identity | None = None

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

    def shaped(self) -> tuple[bool, bool]:
        """
        Say whether the file has shapes, after following any replacement.

        Returns
        -------
        tuple[bool, bool]
            Whether trips has a shape_id column and whether the shapes
            table exists. Both are false for a file built before shapes.
        """
        self.fetch(sql='SELECT 1', params=())
        current = self.identity
        if self.schema is None or self.schema[0] != current:
            rows = self.fetch(sql=SCHEMA_CHECK, params=())
            found = rows[0] if rows else (0, 0)
            self.schema = (
                self.identity,
                bool(found[0]),
                bool(found[1]),
            )
        return self.schema[1], self.schema[2]

    def shape_column(self) -> str:
        """
        The expression that selects a trip's shape ID.

        Returns
        -------
        str
            ``t.shape_id``, or NULL for a file without the column.
        """
        return 't.shape_id' if self.shaped()[0] else NO_SHAPE

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
            point=to_point(lon=lon, lat=lat),
        )

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
        if not self.shaped()[1]:
            return {}
        rows = self.fetch_in(sql=SHAPES_BY_ID, ids=shape_ids)
        return {
            str(shape_id): unpack_shape(
                shape_id=str(shape_id),
                blob=cast(bytes, blob),
            )
            for shape_id, blob in rows
        }

    def modes_near(self, *, box: MetreBox) -> frozenset[Mode]:
        """
        List the modes of the shapes that overlap an area.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north in metres.

        Returns
        -------
        frozenset[Mode]
            The modes; empty for a file built before shapes.
        """
        if not self.shaped()[1]:
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
        return frozenset(Mode(str(row[0])) for row in rows)

    def resolve_trips(
        self, *, trip_ids: Collection[str]
    ) -> dict[str, tuple[object, ...]]:
        """
        Look up trips by ID, falling back to aliases.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, tuple[object, ...]]
            Requested ID to (requested ID, trip ID, route, route type,
            agency, shape ID).
        """
        shape = self.shape_column()
        found = {
            str(row[0]): row
            for row in self.fetch_in(sql=BY_TRIP, ids=trip_ids, shape=shape)
        }
        missing = set(trip_ids) - found.keys()
        for row in self.fetch_in(sql=BY_ALIAS, ids=missing, shape=shape):
            found.setdefault(str(row[0]), row)
        return found

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Stops, shape and running dates of trips.

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
        real = {str(row[1]) for row in found.values()}
        days = self.running_days(trip_ids=real, around=around)
        calls = self.call_stops(trip_ids=real)
        return {
            requested: TripPattern(
                trip_id=requested,
                route=str(row[2]),
                mode=mode_for(
                    route_type=int(str(row[3])),
                    agency_id=str(row[4]) if row[4] else None,
                ),
                shape_id=str(row[5]) if row[5] else None,
                stops=tuple(calls.get(str(row[1]), ())),
                days=tuple(days.get(str(row[1]), ())),
            )
            for requested, row in found.items()
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
        for trip, arr, dep, lon, lat in self.fetch_in(
            sql=PATTERN_STOPS, ids=trip_ids
        ):
            calls.setdefault(str(trip), []).append(
                PatternStop(
                    arrives=int(str(arr)),
                    departs=int(str(dep)),
                    point=to_point(lon=lon, lat=lat),
                )
            )
        return calls

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Route short names by trip or alias.

        Answers are kept until the file is replaced or the cache outgrows
        its limit, so repeat lookups don't query.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs or aliases.

        Returns
        -------
        dict[str, str]
            Short name by ID; unknown IDs are absent.
        """
        self.fetch(sql='SELECT 1', params=())
        if self.identity != self.labels_id:
            self.labels.clear()
            self.labels_id = self.identity
        result = {t: self.labels[t] for t in trip_ids if t in self.labels}
        found = self.look_up_labels(trip_ids=set(trip_ids) - result.keys())
        if len(self.labels) > LABEL_LIMIT:
            self.labels.clear()
        self.labels.update(found)
        return result | found

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

    def stop_name(self, *, stop_id: str) -> str:
        """
        Name of a stop or station.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        str
            Name.

        Raises
        ------
        KeyError
            When the stop isn't in the timetable.
        """
        rows = self.fetch(
            sql='SELECT name FROM stops WHERE stop_id = ?',
            params=(stop_id,),
        )
        if not rows:
            raise KeyError(stop_id)
        return str(rows[0][0])

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
            In departure order.
        """
        first = start.astimezone(SYDNEY).date()
        last = end.astimezone(SYDNEY).date()
        days = {first - timedelta(days=1), first, last}
        found = [
            departure
            for day in sorted(days)
            for departure in self.on_day(
                pin=pin, day=day, start=start, end=end
            )
        ]
        return sorted(found, key=lambda departure: departure.departs)

    def on_day(
        self, *, pin: Pin, day: date, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        List departures from one service day within the range.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        day : date
            Service date.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            Unsorted departures.
        """
        rows = self.fetch(
            sql=DEPARTURES.format(shape=self.shape_column()),
            params={
                'day': day.isoformat(),
                'origin': pin.origin,
                'dest': pin.destination,
                'lo': service_seconds(day=day, moment=start),
                'hi': service_seconds(day=day, moment=end),
            },
        )
        return [to_departure(row=row, day=day) for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()


def to_departure(*, row: tuple[object, ...], day: date) -> Departure:
    """
    Turn a query row into a Departure.

    Parameters
    ----------
    row : tuple[object, ...]
        DEPARTURES row.
    day : date
        Service date the row's times count from.

    Returns
    -------
    Departure
        The departure.
    """
    (trip, route, route_type, agency, headsign, platform, oseq, ostop,
     dseq, dstop, dep, arr, aliases) = row[:13]
    return Departure(
        trip_id=str(trip),
        aliases=tuple(sorted(str(aliases).split())) if aliases else (),
        route=str(route),
        mode=mode_for(
            route_type=int(str(route_type)),
            agency_id=str(agency) if agency else None,
        ),
        headsign=str(headsign),
        platform=str(platform),
        origin_seq=int(str(oseq)),
        origin_stop=str(ostop),
        destination_seq=int(str(dseq)),
        destination_stop=str(dstop),
        departs=service_time(day=day, seconds=int(str(dep))),
        arrives=service_time(day=day, seconds=int(str(arr))),
        service_day=day,
        shape_id=str(row[13]) if row[13] else None,
    )


def to_point(*, lon: object, lat: object) -> Point | None:
    """
    Project a stop's coordinates.

    Parameters
    ----------
    lon : object
        Longitude column.
    lat : object
        Latitude column.

    Returns
    -------
    Point | None
        Metres from the projection origin, or None when either is NULL.
    """
    if lon is None or lat is None:
        return None
    return to_metres(lon=float(str(lon)), lat=float(str(lat)))
