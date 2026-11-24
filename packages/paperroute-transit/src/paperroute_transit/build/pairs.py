"""Pair the bus stops on opposite sides of a road."""

import json
import sqlite3
from collections.abc import Collection
from typing import Final

from paperroute_transit.core.geo import METRES_PER_DEGREE_LAT, Projection
from paperroute_transit.core.modes import is_road

PAIR_RADIUS: Final[float] = 150.0  # metres
PAIR_MARGIN: Final[float] = 0.5  # nearest over runner-up distance
BUSY_HOURLY: Final[int] = 4  # departures an hour, at each kerb's peak
PAIR_SCHEMA: Final[str] = """
CREATE TABLE stop_pairs (station_id TEXT PRIMARY KEY,
  other_id TEXT NOT NULL, busy INTEGER NOT NULL) WITHOUT ROWID;
"""
KERB_ROUTES: Final[str] = """
CREATE TEMP TABLE kerb_routes AS
WITH served AS MATERIALIZED (
  SELECT COALESCE(NULLIF(s.parent_station, ''), s.stop_id) AS place,
    r.short_name AS route, t.direction_id AS way, road(r.type) AS road
  FROM stop_times st JOIN trips t ON t.trip_id = st.trip_id
  JOIN routes r ON r.route_id = t.route_id
  JOIN stops s ON s.stop_id = st.stop_id
  WHERE t.service = 1
  GROUP BY place, route, way, road)
SELECT place, route, MIN(way) AS way FROM served
WHERE place NOT IN (SELECT place FROM served WHERE road = 0)
  AND way IS NOT NULL
GROUP BY place, route HAVING COUNT(DISTINCT way) = 1;
CREATE INDEX temp.kerb_routes_place ON kerb_routes (place, route);
"""
KERB_POINTS: Final[str] = """
CREATE TEMP TABLE kerb_points AS
SELECT p.place, (s.lon - :lon0) * :mx AS x, (s.lat - :lat0) * :my AS y,
  CAST(FLOOR((s.lon - :lon0) * :mx / :cell) AS INTEGER) AS cx,
  CAST(FLOOR((s.lat - :lat0) * :my / :cell) AS INTEGER) AS cy
FROM (SELECT DISTINCT place FROM kerb_routes) p
JOIN stops s ON s.stop_id = p.place
WHERE s.lon IS NOT NULL AND s.lat IS NOT NULL;
CREATE INDEX temp.kerb_points_cell ON kerb_points (cx, cy);
"""
KERB_CANDIDATES: Final[str] = """
WITH near AS (
  SELECT a.place AS one, b.place AS two,
    (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) AS sq
  FROM kerb_points a
  JOIN kerb_points b ON b.cx IN (a.cx - 1, a.cx, a.cx + 1)
    AND b.cy BETWEEN a.cy - 1 AND a.cy + 1 AND b.place <> a.place
  WHERE (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)
    <= :radius * :radius
    AND EXISTS (SELECT 1 FROM kerb_routes ka JOIN kerb_routes kb
      ON kb.place = b.place AND kb.route = ka.route
      WHERE ka.place = a.place AND ka.way <> kb.way)),
ranked AS (
  SELECT one, two, sq,
    ROW_NUMBER() OVER (PARTITION BY one ORDER BY sq, two) AS rank,
    LEAD(sq) OVER (PARTITION BY one ORDER BY sq, two) AS runner_up
  FROM near),
sole AS MATERIALIZED (
  SELECT one, two FROM ranked
  WHERE rank = 1
    AND (runner_up IS NULL OR sq <= :margin * :margin * runner_up))
SELECT s.one, s.two FROM sole s JOIN sole t ON t.one = s.two
WHERE t.two = s.one ORDER BY s.one
"""
REFERENCE_DAY: Final[str] = """
SELECT MIN(date) FROM services
WHERE strftime('%w', date) BETWEEN '1' AND '5'
  AND date NOT IN (SELECT date FROM holidays)
"""
PAIRED_STOPS: Final[str] = """
CREATE TEMP TABLE paired_stops AS
SELECT DISTINCT j.value AS place, s.stop_id
FROM json_each(:places) j
JOIN stops s ON s.stop_id = j.value OR s.parent_station = j.value;
CREATE INDEX temp.paired_stops_stop ON paired_stops (stop_id);
"""
HOURLY_CALLS: Final[str] = """
SELECT place FROM (
  SELECT p.place, COUNT(*) AS calls
  FROM paired_stops p JOIN stop_times st ON st.stop_id = p.stop_id
  JOIN trips t ON t.trip_id = st.trip_id AND t.service = 1
  JOIN services sv ON sv.service_id = t.service_id AND sv.date = :day
  WHERE st.pickup <> 1
  GROUP BY p.place, st.dep / 3600)
GROUP BY place HAVING MAX(calls) >= :busy
"""


def road_flag(route_type: int) -> int:
    """
    Say whether a route type runs on the road.

    Parameters
    ----------
    route_type : int
        GTFS route_type.

    Returns
    -------
    int
        1 for a road route, else 0.
    """
    return int(is_road(route_type=route_type))


def kerb_routes(*, conn: sqlite3.Connection) -> None:
    """
    Make the TEMP table `kerb_routes(place, route, way)`.

    It holds the places no rail route serves, with each route they serve
    in one direction only.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    """
    conn.create_function('road', 1, road_flag, deterministic=True)
    conn.execute('DROP TABLE IF EXISTS temp.kerb_routes')
    conn.executescript(KERB_ROUTES)


def kerb_points(*, conn: sqlite3.Connection, projection: Projection) -> None:
    """
    Make the TEMP table `kerb_points(place, x, y, cx, cy)`.

    It holds each place's position in metres and its grid cell, cells
    being `PAIR_RADIUS` square.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with `kerb_routes`.
    projection : Projection
        The city's projection.
    """
    conn.execute('DROP TABLE IF EXISTS temp.kerb_points')
    params = {
        'lon0': projection.origin_lon,
        'lat0': projection.origin_lat,
        'mx': projection.metres_per_degree_lon,
        'my': METRES_PER_DEGREE_LAT,
        'cell': PAIR_RADIUS,
    }
    statements = KERB_POINTS.split(';\n')
    conn.execute(statements[0], params)
    conn.execute(statements[1])


def kerb_candidates(*, conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """
    List the places that are each other's clearly nearest opposite kerb.

    A kerb's candidate is its nearest opposite kerb when it has no other,
    or when it is at most `PAIR_MARGIN` times as far as the runner-up.

    Each place appears with its candidate, so a pair is listed both ways
    round.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with `kerb_routes` and `kerb_points`.

    Returns
    -------
    list[tuple[str, str]]
        Place and its nearest candidate.
    """
    rows = conn.execute(
        KERB_CANDIDATES, {'radius': PAIR_RADIUS, 'margin': PAIR_MARGIN},
    )
    return list(rows)


def reference_day(*, conn: sqlite3.Connection) -> str | None:
    """
    Pick the first weekday that is not a holiday.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    str | None
        An ISO date, or None when the window has no such day.
    """
    day: str | None = conn.execute(REFERENCE_DAY).fetchone()[0]
    return day


def busy_places(
    *, conn: sqlite3.Connection, places: Collection[str], day: str | None,
) -> frozenset[str]:
    """
    Find the places with `BUSY_HOURLY` boardings in one clock hour.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    places : Collection[str]
        Places to test.
    day : str | None
        The date to count on.

    Returns
    -------
    frozenset[str]
        The busy places; empty with no day or no places.
    """
    if day is None or not places:
        return frozenset()
    conn.execute('DROP TABLE IF EXISTS temp.paired_stops')
    statements = PAIRED_STOPS.split(';\n')
    conn.execute(statements[0], {'places': json.dumps(sorted(places))})
    conn.execute(statements[1])
    rows = conn.execute(HOURLY_CALLS, {'day': day, 'busy': BUSY_HOURLY})
    found = frozenset(row[0] for row in rows)
    conn.execute('DROP TABLE IF EXISTS temp.paired_stops')
    return found


def load_pairs(*, conn: sqlite3.Connection, projection: Projection) -> int:
    """
    Fill `stop_pairs` with the opposite kerbs, each stored both ways.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the `stop_pairs` table.
    projection : Projection
        The city's projection.

    Returns
    -------
    int
        Rows written.
    """
    kerb_routes(conn=conn)
    kerb_points(conn=conn, projection=projection)
    candidates = kerb_candidates(conn=conn)
    busy = busy_places(
        conn=conn,
        places={one for one, _ in candidates},
        day=reference_day(conn=conn),
    )
    conn.executemany(
        'INSERT INTO stop_pairs VALUES (?, ?, ?)',
        [
            (one, two, int(one in busy and two in busy))
            for one, two in candidates
        ],
    )
    conn.execute('DROP TABLE IF EXISTS temp.kerb_points')
    conn.execute('DROP TABLE IF EXISTS temp.kerb_routes')
    return len(candidates)
