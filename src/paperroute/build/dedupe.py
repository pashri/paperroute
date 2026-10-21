"""Drop bundle rail trips that the Sydney Trains or Metro feeds also carry."""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

from paperroute.core.model import REGIONAL_AGENCIES, TRAINLINK

MIN_SHARED: Final[int] = 2

KEYS: Final[str] = """
CREATE TEMP TABLE trip_key AS
SELECT t.trip_id, t.source, t.service_id, r.short_name, r.agency_id,
  (SELECT COALESCE(s.parent_station, s.stop_id) FROM stop_times st
     JOIN stops s ON s.stop_id = st.stop_id
     WHERE st.trip_id = t.trip_id AND (st.pickup <> 1 OR st.dropoff <> 1)
     ORDER BY st.seq LIMIT 1) AS first_station,
  (SELECT st.dep FROM stop_times st WHERE st.trip_id = t.trip_id
     AND (st.pickup <> 1 OR st.dropoff <> 1)
     ORDER BY st.seq LIMIT 1) AS first_dep,
  (SELECT COALESCE(s.parent_station, s.stop_id) FROM stop_times st
     JOIN stops s ON s.stop_id = st.stop_id
     WHERE st.trip_id = t.trip_id AND (st.pickup <> 1 OR st.dropoff <> 1)
     ORDER BY st.seq DESC LIMIT 1) AS last_station
FROM trips t JOIN routes r ON r.route_id = t.route_id
WHERE r.type IN (2, 106, 401) AND t.service = 1;
"""
MATCHES: Final[str] = """
CREATE TEMP TABLE candidate AS
SELECT b.trip_id AS dup_id, p.trip_id AS keep_id, bs.date AS date,
  ROW_NUMBER() OVER (PARTITION BY b.trip_id, bs.date
    ORDER BY ABS(p.first_dep - b.first_dep),
      p.short_name = b.short_name DESC, p.trip_id) AS rank
FROM trip_key b
JOIN services bs ON bs.service_id = b.service_id
JOIN trip_key p ON p.source <> 'bundle' AND p.first_station = b.first_station
  AND p.last_station = b.last_station
  AND ABS(p.first_dep - b.first_dep) <= 60
JOIN services ps ON ps.service_id = p.service_id AND ps.date = bs.date
WHERE b.source = 'bundle';
CREATE TEMP TABLE pair AS
SELECT dup_id, keep_id, date FROM candidate WHERE rank = 1;
CREATE TEMP TABLE match AS
SELECT b.trip_id AS dup_id, COUNT(DISTINCT pair.date) AS covered,
  (SELECT COUNT(*) FROM services x WHERE x.service_id = b.service_id) AS runs
FROM pair JOIN trip_key b ON b.trip_id = pair.dup_id
GROUP BY b.trip_id;
"""
APPLY: Final[str] = """
BEGIN;
INSERT INTO trip_aliases (alias_id, trip_id)
  SELECT DISTINCT dup_id, keep_id FROM pair;
INSERT INTO trip_skip (trip_id, date)
  SELECT dup_id, date FROM pair
  WHERE dup_id IN (SELECT dup_id FROM match WHERE covered < runs);
UPDATE trips SET agency_id = (
  SELECT MIN(b.agency_id) FROM trip_aliases a
  JOIN trip_key b ON b.trip_id = a.alias_id
  WHERE a.trip_id = trips.trip_id)
WHERE trip_id IN (SELECT trip_id FROM trip_aliases);
DELETE FROM stop_times WHERE trip_id IN
  (SELECT dup_id FROM match WHERE covered = runs);
DELETE FROM trips WHERE trip_id IN
  (SELECT dup_id FROM match WHERE covered = runs);
COMMIT;
"""
CLEANUP: Final[str] = """
DROP TABLE candidate;
DROP TABLE pair;
DROP TABLE match;
DROP TABLE trip_key;
"""


@dataclass(frozen=True)
class MergeReport:
    """What merging rail trips did."""

    dropped: int
    partial: int


def merge_rail(*, conn: sqlite3.Connection) -> MergeReport:
    """
    Drop bundle rail and metro trips duplicated by a preferred feed.

    A bundle rail trip (route type 2, 106 or 401) is a duplicate on a date
    when a non-bundle trip running that date has the same first and last
    calling station and a first calling departure within a minute. Line
    names are not compared: NSW TrainLink's XPTs are lines 31-36 and 621-644
    in the bundle but NRC, NRW, STH and WST in the Sydney Trains feed, and
    their times can differ by a second. On each date the nearest departure
    wins, then the same line name. Rows where the train passes without
    stopping (no pickup and no drop-off) are ignored.

    A trip duplicated on every date it runs is dropped. One duplicated on
    only some dates is kept, and each covered date goes into trip_skip so
    the trip is not listed on that date.

    The bundle ID becomes an alias of every trip that covers it on any
    date, so realtime notes using it still find the trip, and the kept trip
    takes the bundle copy's agency as its agency of record: an XPT kept from
    the Sydney Trains feed stays Regional and is matched in the nswtrains
    feed.

    The changes are applied in one transaction and committed before
    returning.

    Parameters
    ----------
    conn : sqlite3.Connection
        Loaded timetable.

    Returns
    -------
    MergeReport
        Trips dropped, and bundle trips matched on only some dates.
    """
    conn.executescript(KEYS + MATCHES)
    dropped, partial = conn.execute(
        'SELECT SUM(covered = runs), SUM(covered < runs) FROM match'
    ).fetchone()
    try:
        conn.executescript(APPLY)
    except sqlite3.Error:
        conn.rollback()
        raise
    conn.executescript(CLEANUP)
    return MergeReport(dropped=int(dropped or 0), partial=int(partial or 0))


STATIONS_OF_ROUTES: Final[str] = """
SELECT DISTINCT r.route_id, r.short_name, r.long_name, r.type,
  COALESCE(s.parent_station, s.stop_id)
FROM routes r JOIN trips t ON t.route_id = r.route_id
  JOIN stop_times st ON st.trip_id = t.trip_id
  JOIN stops s ON s.stop_id = st.stop_id
WHERE ((r.type = 106 AND r.agency_id IN ({agencies}))
  OR (r.type = 2 AND r.short_name IN ({lines})))
  AND (st.pickup <> 1 OR st.dropoff <> 1) AND t.service = 1
ORDER BY r.route_id
"""


def stations_by_route(
    *, conn: sqlite3.Connection,
) -> tuple[
    dict[str, set[str]], dict[str, str], dict[str, set[str]],
]:
    """
    Read the stations of each TrainLink line and each bundle 106 route.

    Parameters
    ----------
    conn : sqlite3.Connection
        Loaded timetable.

    Returns
    -------
    tuple[dict[str, set[str]], dict[str, str], dict[str, set[str]]]
        Parent stations by NSWTrains line name; the first long name by
        line name; parent stations by bundle route ID.
    """
    sql = STATIONS_OF_ROUTES.format(
        agencies=', '.join('?' * len(REGIONAL_AGENCIES)),
        lines=', '.join('?' * len(TRAINLINK)),
    )
    lines: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    routes: dict[str, set[str]] = defaultdict(set)
    for route_id, short, long, kind, station in conn.execute(
        sql, (*sorted(REGIONAL_AGENCIES), *TRAINLINK),
    ):
        if kind == 2:
            lines[short].add(station)
            names.setdefault(short, long)
        else:
            routes[route_id].add(station)
    return lines, names, routes


def closest_line(
    *, shared: dict[str, int],
) -> str | None:
    """
    Pick the TrainLink line that shares the most stations.

    Parameters
    ----------
    shared : dict[str, int]
        Stations shared with each line, by line name.

    Returns
    -------
    str | None
        The line with the most, the first in TRAINLINK order on a tie;
        None when no line shares enough stations to be the same line.
    """
    best = max(shared.values(), default=0)
    if best < MIN_SHARED:
        return None
    return next(line for line in TRAINLINK if shared.get(line) == best)


def relabel_trainlink(*, conn: sqlite3.Connection) -> int:
    """
    Name each bundle NSW TrainLink train route after its NSWTrains line.

    The bundle numbers these services (31 to 36, 621 to 644) while the
    Sydney Trains feed names the same lines NRC, NRW, STH and WST. A
    bundle route takes the name and long name of the line whose trips
    call at the most of the same parent stations. A route sharing too few
    stations with any line keeps its number.

    Parameters
    ----------
    conn : sqlite3.Connection
        Loaded timetable, after `merge_rail`.

    Returns
    -------
    int
        Number of routes renamed.
    """
    lines, names, routes = stations_by_route(conn=conn)
    renamed = 0
    for route_id, stations in routes.items():
        line = closest_line(shared={
            code: len(stations & lines[code]) for code in names
        })
        if line is not None:
            conn.execute(
                'UPDATE routes SET short_name = ?, long_name = ? '
                'WHERE route_id = ?',
                (line, names[line], route_id),
            )
            renamed += 1
    conn.commit()
    return renamed
