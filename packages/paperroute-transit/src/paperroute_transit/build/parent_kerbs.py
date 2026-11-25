"""Record the two bus kerbs of each parent station whose buses part ways."""

import json
import sqlite3
from typing import Final

from paperroute_transit.build.line_stations import Spot, metres_apart
from paperroute_transit.build.pairs import (
    busy_places,
    reference_day,
    road_flag,
)

PARENT_KERB_SCHEMA: Final[str] = """
CREATE TABLE parent_kerbs (stop_id TEXT PRIMARY KEY,
  station_id TEXT NOT NULL, other_id TEXT NOT NULL, onward TEXT NOT NULL,
  trips INTEGER NOT NULL, busy INTEGER NOT NULL) WITHOUT ROWID;
CREATE INDEX parent_kerbs_station ON parent_kerbs (station_id);
"""
KERBS_APART: Final[float] = 1000.0  # metres
TWO_KERBS: Final[str] = """
CREATE TEMP TABLE two_kerbs AS
WITH called AS (
  SELECT s.stop_id, s.parent_station AS station, MIN(road(r.type)) AS road
  FROM stops s JOIN stop_times st ON st.stop_id = s.stop_id
  JOIN trips t ON t.trip_id = st.trip_id AND t.service = 1
  JOIN routes r ON r.route_id = t.route_id
  WHERE COALESCE(s.parent_station, '') <> ''
  GROUP BY s.stop_id)
SELECT stop_id, station FROM called WHERE station IN (
  SELECT station FROM called GROUP BY station
  HAVING COUNT(*) = 2 AND MIN(road) = 1)
"""
KERB_ONWARD: Final[str] = """
SELECT o.stop_id, (
  SELECT COALESCE(NULLIF(s.parent_station, ''), s.stop_id)
  FROM stop_times n JOIN stops s ON s.stop_id = n.stop_id
  WHERE n.trip_id = o.trip_id AND n.seq > o.seq
    AND NOT (n.pickup = 1 AND n.dropoff = 1)
    AND COALESCE(NULLIF(s.parent_station, ''), s.stop_id) <> k.station
  ORDER BY n.seq LIMIT 1), COUNT(*)
FROM stop_times o JOIN two_kerbs k ON k.stop_id = o.stop_id
JOIN trips t ON t.trip_id = o.trip_id AND t.service = 1
WHERE o.pickup <> 1
GROUP BY o.stop_id, 2
"""


def facing_ways(*, here: frozenset[str], there: frozenset[str]) -> bool:
    """
    Say whether two kerbs' buses leave different ways.

    Parameters
    ----------
    here : frozenset[str]
        Stations one kerb's buses call at next.
    there : frozenset[str]
        Stations the other kerb's buses call at next.

    Returns
    -------
    bool
        True when both sets hold a station and they share none.
    """
    return bool(here) and bool(there) and not here & there


def close_pairs(
    *,
    conn: sqlite3.Connection,
    pairs: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """
    Keep the pairs whose two kerbs are within `KERBS_APART` metres.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    pairs : list[tuple[str, str, str]]
        Station and its two kerbs.

    Returns
    -------
    list[tuple[str, str, str]]
        The pairs whose kerbs are close; a kerb with no coordinates
        drops its pair.
    """
    spots = {
        stop_id: Spot(name=stop_id, lat=lat, lon=lon)
        for stop_id, lat, lon in conn.execute(
            'SELECT stop_id, lat, lon FROM stops'
            ' WHERE lat IS NOT NULL AND lon IS NOT NULL',
        )
    }
    return [
        (station, one, two)
        for station, one, two in pairs
        if one in spots
        and two in spots
        and metres_apart(first=spots[one], second=spots[two]) <= KERBS_APART
    ]


def make_two_kerbs(*, conn: sqlite3.Connection) -> None:
    """
    Make the TEMP table `two_kerbs(stop_id, station)`.

    It holds the stops of each parent station that service trips call at
    on exactly two stops, every such call being on a road route.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    """
    conn.create_function('road', 1, road_flag, deterministic=True)
    conn.execute('DROP TABLE IF EXISTS temp.two_kerbs')
    conn.execute(TWO_KERBS)


def kerb_onward(
    *, conn: sqlite3.Connection,
) -> tuple[dict[str, frozenset[str]], dict[str, int]]:
    """
    Read where each kerb in `two_kerbs` sends its buses next.

    A next call at the kerb's own station, or one that neither picks up
    nor sets down, is passed over.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with `two_kerbs`.

    Returns
    -------
    tuple[dict[str, frozenset[str]], dict[str, int]]
        Each kerb's next stations (parent, else stop), then the service
        trips leaving it.
    """
    onward: dict[str, set[str]] = {}
    trips: dict[str, int] = {}
    for stop_id, station, count in conn.execute(KERB_ONWARD):
        trips[stop_id] = trips.get(stop_id, 0) + count
        stations = onward.setdefault(stop_id, set())
        if station is not None:
            stations.add(station)
    return (
        {stop_id: frozenset(found) for stop_id, found in onward.items()},
        trips,
    )


def load_parent_kerbs(*, conn: sqlite3.Connection) -> int:
    """
    Fill `parent_kerbs` with the parents whose two kerbs face apart.

    A parent whose kerbs are over a kilometre apart is left out.

    Each kerb is one row naming the other kerb, its next stations as a
    sorted JSON array, its trips and whether both kerbs are busy.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the `parent_kerbs` table.

    Returns
    -------
    int
        Parent stations written.
    """
    make_two_kerbs(conn=conn)
    onward, trips = kerb_onward(conn=conn)
    kerbs: dict[str, list[str]] = {}
    for stop_id, station in conn.execute(
        'SELECT stop_id, station FROM two_kerbs ORDER BY stop_id',
    ):
        kerbs.setdefault(station, []).append(stop_id)
    pairs = close_pairs(
        conn=conn,
        pairs=[
            (station, one, two)
            for station, (one, two) in kerbs.items()
            if facing_ways(
                here=onward.get(one, frozenset()),
                there=onward.get(two, frozenset()),
            )
        ],
    )
    busy = busy_places(
        conn=conn,
        places={kerb for _, one, two in pairs for kerb in (one, two)},
        day=reference_day(conn=conn),
    )
    conn.executemany(
        'INSERT INTO parent_kerbs VALUES (?, ?, ?, ?, ?, ?)',
        [
            (
                here, station, there, json.dumps(sorted(onward[here])),
                trips[here], int(one in busy and two in busy),
            )
            for station, one, two in pairs
            for here, there in ((one, two), (two, one))
        ],
    )
    conn.execute('DROP TABLE IF EXISTS temp.two_kerbs')
    return len(pairs)
