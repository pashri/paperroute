"""Write each station-first line's station list and station aliases."""

import json
import math
import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from typing import Final

from paperroute.build.gtfs_files import insert_many
from paperroute.build.line_order import line_order
from paperroute.build.runs import collapsed
from paperroute.core.model import until_repeated_hop

SAME_PLACE: Final[float] = 300.0  # metres
EARTH_RADIUS: Final[float] = 6_371_000.0  # metres
LINE_CALLS: Final[str] = """
SELECT st.trip_id, t.route_id,
  COALESCE(NULLIF(s.parent_station, ''), s.stop_id)
FROM trip_runs r JOIN trips t USING (trip_id)
  JOIN stop_times st USING (trip_id) JOIN stops s ON s.stop_id = st.stop_id
WHERE t.route_id IN (SELECT value FROM json_each(?))
  AND (st.pickup <> 1 OR st.dropoff <> 1)
ORDER BY st.trip_id, st.seq
"""
STATION_SPOTS: Final[str] = """
SELECT stop_id, name, lat, lon FROM stops
WHERE stop_id IN (SELECT value FROM json_each(?)) AND lat IS NOT NULL
  AND lon IS NOT NULL
"""
type Patterns = list[tuple[tuple[str, ...], int]]


@dataclass(frozen=True)
class Spot:
    """A station's name and where it is."""

    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class LineInputs:
    """Every line's patterns and the stations they call at."""

    patterns: dict[str, Patterns]
    spots: dict[str, Spot]
    aliases: dict[str, str]


def line_patterns(
    *, conn: sqlite3.Connection, keys: Mapping[str, str],
) -> dict[str, Patterns]:
    """
    Count each line's stopping patterns.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    keys : Mapping[str, str]
        Line key by route ID.

    Returns
    -------
    dict[str, Patterns]
        Station sequences and trips by line key. Only calls that pick up
        or set down count, so a train running through a station without
        stopping does not put it on the line. Only trips with a run count.
    """
    found: dict[str, Counter[tuple[str, ...]]] = {}
    rows = conn.execute(LINE_CALLS, (json.dumps(sorted(keys)),))
    for _, calls in groupby(rows, key=itemgetter(0)):
        listed = list(calls)
        stations = until_repeated_hop(stations=collapsed(
            stations=(station for _, _, station in listed),
        ))
        found.setdefault(keys[listed[0][1]], Counter())[stations] += 1
    return {key: list(counts.items()) for key, counts in found.items()}


def metres_apart(*, first: Spot, second: Spot) -> float:
    """
    Measure the distance between two nearby stations.

    Parameters
    ----------
    first : Spot
        One station.
    second : Spot
        The other station.

    Returns
    -------
    float
        Metres, on a flat projection at their mean latitude.
    """
    middle = math.radians((first.lat + second.lat) / 2)
    east = math.radians(second.lon - first.lon) * math.cos(middle)
    north = math.radians(second.lat - first.lat)
    return EARTH_RADIUS * math.hypot(east, north)


def station_aliases(
    *, spots: Mapping[str, Spot], trips: Counter[str],
) -> dict[str, str]:
    """
    Merge stations that share a name and sit within SAME_PLACE.

    Parameters
    ----------
    spots : Mapping[str, Spot]
        Name and place by station ID.
    trips : Counter[str]
        Trips calling at each station.

    Returns
    -------
    dict[str, str]
        The kept station by merged station ID: the busiest same-name
        station within SAME_PLACE, ties to the smallest ID. Kept
        stations are left out.
    """
    kept: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    for station in sorted(spots, key=lambda s: (-trips[s], s)):
        spot = spots[station]
        near = next((
            other
            for other in kept.get(spot.name, [])
            if metres_apart(first=spot, second=spots[other]) <= SAME_PLACE
        ), None)
        if near is None:
            kept.setdefault(spot.name, []).append(station)
        else:
            aliases[station] = near
    return aliases


def line_inputs(
    *, conn: sqlite3.Connection, keys: Mapping[str, str],
) -> LineInputs:
    """
    Read every line's patterns, with same-place stations merged.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    keys : Mapping[str, str]
        Line key by route ID.

    Returns
    -------
    LineInputs
        Patterns by line key with merged stations replaced by the kept
        one, the stations' names and places, and the merges made.
    """
    raw = line_patterns(conn=conn, keys=keys)
    trips: Counter[str] = Counter()
    for patterns in raw.values():
        for stations, count in patterns:
            trips.update(dict.fromkeys(stations, count))
    spots = {
        station: Spot(name=name, lat=lat, lon=lon)
        for station, name, lat, lon in conn.execute(
            STATION_SPOTS, (json.dumps(sorted(trips)),),
        )
    }
    aliases = station_aliases(spots=spots, trips=trips)
    return LineInputs(patterns={
        key: [
            (collapsed(stations=(aliases.get(s, s) for s in stations)), n)
            for stations, n in patterns
        ]
        for key, patterns in raw.items()
    }, spots=spots, aliases=aliases)


def line_station_rows(
    *, inputs: LineInputs,
) -> Iterator[tuple[str, int, str, str]]:
    """
    Give the line_stations rows of every line.

    Parameters
    ----------
    inputs : LineInputs
        Lines from line_inputs.

    Yields
    ------
    tuple[str, int, str, str]
        Line key, position from 0, station ID and head.
    """
    latitude = {station: spot.lat for station, spot in inputs.spots.items()}
    for key in sorted(inputs.patterns):
        order = line_order(
            patterns=inputs.patterns[key], latitude=latitude,
        )
        for pos, (station, head) in enumerate(order):
            yield key, pos, station, head


def write_line_stations(
    *, conn: sqlite3.Connection, keys: Mapping[str, str],
) -> None:
    """
    Write the line_stations and line_aliases rows.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables and the catalogue tables.
    keys : Mapping[str, str]
        Line key by route ID of every station-first line.
    """
    inputs = line_inputs(conn=conn, keys=keys)
    insert_many(conn=conn, rows=line_station_rows(inputs=inputs), sql=(
        'INSERT INTO line_stations VALUES (?, ?, ?, ?)'
    ))
    insert_many(conn=conn, rows=sorted(inputs.aliases.items()), sql=(
        'INSERT INTO line_aliases VALUES (?, ?)'
    ))
