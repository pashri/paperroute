"""Read the runs through a station and the kerbs at it."""

import json
from collections.abc import Collection, Iterable, Sequence
from typing import Final, cast

from paperroute_transit.adapters.network_lines import Fetch, HasTables, Row
from paperroute_transit.adapters.timetable_db import to_point
from paperroute_transit.core.geo import Projection
from paperroute_transit.core.model import StopPlace, until_revisit
from paperroute_transit.core.reach import (
    RunTail,
    StripKey,
    every_route,
    merge_tails,
)
from paperroute_transit.core.sides import KerbPair, ParentKerb, ParentKerbs

KERB_PAIRS: Final[str] = """
SELECT p.station_id, p.other_id, p.busy, s.name, s.lon, s.lat
FROM stop_pairs p JOIN stops s ON s.stop_id = p.other_id
WHERE p.station_id IN (SELECT value FROM json_each(:ids))
"""
PARENT_KERBS: Final[str] = """
SELECT k.station_id, k.stop_id, s.name, s.lon, s.lat, k.onward, k.trips,
  k.busy
FROM parent_kerbs k JOIN stops s ON s.stop_id = k.stop_id
WHERE k.station_id IN (SELECT value FROM json_each(:ids))
ORDER BY k.station_id, k.stop_id
"""
APPROACH_ROADS: Final[str] = """
SELECT DISTINCT stop_id, road FROM approach_roads
WHERE station_id IN (
  SELECT COALESCE(NULLIF(s.parent_station, ''), s.stop_id) FROM stops s
  WHERE s.stop_id IN (SELECT value FROM json_each(:ids)))
ORDER BY stop_id, road
"""
RUNS_THROUGH: Final[str] = """
SELECT r.stations, r.trips, s.pos
FROM run_stops s JOIN runs r ON r.run_id = s.run_id
WHERE s.station_id = :station
  AND (:kind IS NULL OR r.kind = :kind)
  AND (:any_route OR r.route IN (SELECT value FROM json_each(:routes)))
ORDER BY s.run_id, s.pos
"""


def parent_kerb(
    *, row: Sequence[object], projection: Projection
) -> ParentKerb:
    """
    Make one kerb from the middle six columns of a `PARENT_KERBS` row.

    Parameters
    ----------
    row : Sequence[object]
        Station, stop ID, name, lon, lat, onward JSON, trips, busy.
    projection : Projection
        The projection the point is placed in.

    Returns
    -------
    ParentKerb
        The kerb.
    """
    _, stop_id, name, lon, lat, onward, trips, _ = row
    return ParentKerb(
        place=StopPlace(
            stop_id=str(stop_id),
            name=str(name),
            point=to_point(lon=lon, lat=lat, projection=projection),
        ),
        onward=frozenset(json.loads(str(onward))),
        trips=cast(int, trips),
    )


def merged_tails(*, rows: Iterable[Row]) -> tuple[RunTail, ...]:
    """
    Make one tail per pass of the From and merge identical tails.

    Each tail is cut before the first station it would call at again
    after the From; stations before the From never cut it. Tails are
    then merged as `merge_tails` does.

    Parameters
    ----------
    rows : Iterable[Row]
        Each run's stations as JSON, its trip-days and the From's
        position in it, one row per pass.

    Returns
    -------
    tuple[RunTail, ...]
        Tails, the most trips first, then by their stations.
    """
    passes = (
        (tuple(json.loads(str(stations))), cast(int, trips), cast(int, pos))
        for stations, trips, pos in rows
    )
    return merge_tails(tails=(
        RunTail(
            before=calls[:at],
            after=until_revisit(stations=calls[at:])[1:],
            trips=weight,
        )
        for calls, weight, at in passes
    ))


class StationRuns:
    """
    Read the runs through a station and the kerbs at it.

    Parameters
    ----------
    fetch : Fetch
        Runs queries against the timetable file.
    has_tables : HasTables
        Says whether the file has some tables.
    projection : Projection
        The projection stop points are placed in.
    line_kinds : frozenset[str]
        The city's modes chosen by line.
    """

    def __init__(
        self,
        *,
        fetch: Fetch,
        has_tables: HasTables,
        projection: Projection,
        line_kinds: frozenset[str],
    ) -> None:
        self.fetch = fetch
        self.has_tables = has_tables
        self.projection = projection
        self.line_kinds = line_kinds

    def kerb_pairs(
        self, *, stations: Collection[str]
    ) -> dict[str, KerbPair]:
        """
        Read the kerb across the road from each station that has one.

        Parameters
        ----------
        stations : Collection[str]
            Station IDs.

        Returns
        -------
        dict[str, KerbPair]
            Pair by station ID; empty for an old file.
        """
        if not self.has_tables(tables=('stop_pairs',)):
            return {}
        rows = self.fetch(
            sql=KERB_PAIRS, params={'ids': json.dumps(list(stations))},
        )
        return {
            str(station): KerbPair(
                station=str(station),
                other=StopPlace(
                    stop_id=str(other),
                    name=str(name),
                    point=to_point(
                        lon=lon,
                        lat=lat,
                        projection=self.projection,
                    ),
                ),
                busy=bool(busy),
            )
            for station, other, busy, name, lon, lat in rows
        }

    def parent_kerbs(
        self, *, stations: Collection[str]
    ) -> dict[str, ParentKerbs]:
        """
        Read the two kerbs of each parent station that has them.

        Parameters
        ----------
        stations : Collection[str]
            Station IDs.

        Returns
        -------
        dict[str, ParentKerbs]
            Kerbs by station ID; empty for an old file. A station with
            other than two kerb rows is left out.
        """
        if not self.has_tables(tables=('parent_kerbs',)):
            return {}
        rows = self.fetch(
            sql=PARENT_KERBS, params={'ids': json.dumps(list(stations))},
        )
        grouped: dict[str, list[Row]] = {}
        for row in rows:
            grouped.setdefault(str(row[0]), []).append(row)
        return {
            station: ParentKerbs(
                station=station,
                kerbs=(
                    parent_kerb(row=group[0], projection=self.projection),
                    parent_kerb(row=group[1], projection=self.projection),
                ),
                busy=bool(group[0][7]),
            )
            for station, group in grouped.items()
            if len(group) == 2
        }

    def approach_roads(
        self, *, stations: Collection[str]
    ) -> dict[str, tuple[str, ...]]:
        """
        Read the roads buses arrive at the stations' parents by.

        Parameters
        ----------
        stations : Collection[str]
            Station or kerb IDs; a kerb stands for its parent.

        Returns
        -------
        dict[str, tuple[str, ...]]
            Roads by the place called at just before the parent; empty
            for an old file.
        """
        if not self.has_tables(tables=('approach_roads',)):
            return {}
        found: dict[str, tuple[str, ...]] = {}
        for place, road in self.fetch(
            sql=APPROACH_ROADS, params={'ids': json.dumps(list(stations))},
        ):
            found[str(place)] = (*found.get(str(place), ()), str(road))
        return found

    def tails(
        self, *, station: str, key: StripKey
    ) -> tuple[RunTail, ...]:
        """
        Read the tails of the runs through a station that feed a strip.

        Train, Metro and Light rail take every run of their kind; other
        kinds only their routes' runs, or every run of the kind when no
        routes are chosen; no kind takes every run.

        Parameters
        ----------
        station : str
            Station ID.
        key : StripKey
            Strip kind and routes.

        Returns
        -------
        tuple[RunTail, ...]
            Merged tails.
        """
        rows = self.fetch(sql=RUNS_THROUGH, params={
            'station': station,
            'kind': None if key.kind is None else str(key.kind),
            'any_route': every_route(key=key, line_kinds=self.line_kinds),
            'routes': json.dumps(list(key.routes)),
        })
        return merged_tails(rows=rows)
