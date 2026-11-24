"""Timetable repository over the built SQLite file."""

import math
from collections.abc import Collection
from datetime import date
from typing import cast

from paperroute_transit.adapters.alert_rows import (
    alert_places,
    read_agency_stops,
)
from paperroute_transit.adapters.departure_rows import STOP_STATION
from paperroute_transit.adapters.timetable_base import (
    DEFAULT_PATH,
    LABEL_LIMIT,
    MATCH_RADIUS,
    MODES_NEAR,
    NAMED_NEAR,
    RAIL_HOPS_FROM,
    SHAPES_BY_ID,
    to_point,
)
from paperroute_transit.adapters.timetable_departures import DepartureQueries
from paperroute_transit.adapters.timetable_trips import TripQueries
from paperroute_transit.core.alert_model import AlertPlaces
from paperroute_transit.core.geo import (
    MetreBox,
    Point,
    Shape,
    to_lonlat,
    unpack_shape,
)
from paperroute_transit.core.model import StopPlace

__all__ = [
    'DEFAULT_PATH',
    'LABEL_LIMIT',
    'SqliteTimetableRepository',
    'to_point',
]


# pylint: disable-next=too-many-public-methods
class SqliteTimetableRepository(TripQueries, DepartureQueries):
    """Read-only queries against the timetable file."""

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

    def station_for(self, *, stop_id: str) -> str:
        """
        Find the station a stop belongs to.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        str
            Its parent station's ID, or the ID itself.
        """
        rows = self.fetch(sql=STOP_STATION, params=(stop_id,))
        return str(rows[0][0]) if rows else stop_id

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

    def rail_hops(
        self, *, pairs: Collection[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[Point, ...]]:
        """
        Track paths between pairs of stations.

        Parameters
        ----------
        pairs : Collection[tuple[str, str]]
            Station IDs, in running order.

        Returns
        -------
        dict[tuple[str, str], tuple[Point, ...]]
            The path of each pair held inside the track, from the first
            station to the second; a hop stored the other way round comes
            back reversed. Empty for a file with no rail_hops table.
        """
        if not pairs or not self.features().hops:
            return {}
        stations = {station for pair in pairs for station in pair}
        found: dict[tuple[str, str], tuple[Point, ...]] = {}
        for start, end, blob in self.fetch_in(
            sql=RAIL_HOPS_FROM, ids=stations,
        ):
            key = (str(start), str(end))
            if key not in pairs and key[::-1] not in pairs:
                continue
            points = unpack_shape(shape_id='', blob=cast(bytes, blob)).points
            if key in pairs:
                found[key] = points
            if key[::-1] in pairs:
                found.setdefault(key[::-1], tuple(reversed(points)))
        return found

    def modes_near(self, *, box: MetreBox) -> frozenset[str]:
        """
        List the modes of the shapes that overlap an area.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north in metres.

        Returns
        -------
        frozenset[str]
            The feed sources; empty for a file built before shapes.
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
        return frozenset(str(row[0]) for row in rows)


    def alert_places(
        self,
        *,
        route_ids: Collection[str],
        stop_ids: Collection[str],
        route_type: int | None = None,
    ) -> AlertPlaces:
        """
        Look up the routes and stops an alert feed names.

        Parameters
        ----------
        route_ids : Collection[str]
            Route IDs from the feed.
        stop_ids : Collection[str]
            Stop IDs from the feed.
        route_type : int | None
            The GTFS type whose only route to report, or None.

        Returns
        -------
        AlertPlaces
            Route short names, stop families and the only line of
            `route_type`; unknown IDs are absent.
        """
        return alert_places(
            route_ids=route_ids,
            stop_ids=stop_ids,
            prefixes=self.route_prefixes,
            route_type=route_type,
            fetch=lambda sql, params: self.fetch(sql=sql, params=params),
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

    def serves_agencies(
        self, *, stop_ids: Collection[str], agencies: Collection[str],
    ) -> bool:
        """
        Say whether a trip of some agencies calls at any of some stops.

        Parameters
        ----------
        stop_ids : Collection[str]
            Stop IDs.
        agencies : Collection[str]
            GTFS agency IDs.

        Returns
        -------
        bool
            True when one does.
        """
        return read_agency_stops(
            stop_ids=stop_ids,
            agencies=agencies,
            fetch_in=lambda sql, ids, tail: self.fetch_in(
                sql=sql, ids=ids, tail=tail,
            ),
        )
