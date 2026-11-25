"""Record the road each bus arrives at a parent station's kerbs by."""

import sqlite3
from typing import Final

from paperroute_transit.build.pairs import road_flag
from paperroute_transit.core.names import NameRules, base_name
from paperroute_transit.core.streets import street_of

APPROACH_ROAD_SCHEMA: Final[str] = """
CREATE TABLE approach_roads (station_id TEXT, stop_id TEXT, road TEXT,
  PRIMARY KEY (station_id, stop_id, road)) WITHOUT ROWID;
"""
CALLS_BEFORE: Final[str] = """
WITH calls AS (
  SELECT st.stop_id,
    LAG(st.stop_id) OVER (PARTITION BY st.trip_id ORDER BY st.seq) AS prev
  FROM stop_times st JOIN trips t ON t.trip_id = st.trip_id
  JOIN routes r ON r.route_id = t.route_id
  WHERE t.service = 1 AND road(r.type) = 1)
SELECT DISTINCT k.parent_station, w.stop_id, w.name
FROM calls c JOIN stops k ON k.stop_id = c.stop_id
JOIN stops p ON p.stop_id = c.prev
JOIN stops w ON w.stop_id = COALESCE(NULLIF(p.parent_station, ''), p.stop_id)
WHERE COALESCE(k.parent_station, '') <> ''
  AND w.stop_id <> k.parent_station
"""


def road_name(*, name: str, rules: NameRules) -> str:
    """
    Name the road a stop is on.

    Parameters
    ----------
    name : str
        The stop's name.
    rules : NameRules
        The city's name rules.

    Returns
    -------
    str
        Its street (see `street_of`), else its base name.
    """
    return street_of(name=name, rules=rules) or base_name(name=name)


def load_approach_roads(*, conn: sqlite3.Connection, rules: NameRules) -> int:
    """
    Fill `approach_roads` from the call before each parent's kerbs.

    Each row is a parent station, the place a road trip calls at just
    before one of its kerbs, and that place's road.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the `approach_roads` table.
    rules : NameRules
        The city's name rules.

    Returns
    -------
    int
        Rows written.
    """
    conn.create_function('road', 1, road_flag, deterministic=True)
    rows = {
        (str(station), str(place), road_name(name=str(name), rules=rules))
        for station, place, name in conn.execute(CALLS_BEFORE)
    }
    conn.executemany('INSERT INTO approach_roads VALUES (?, ?, ?)', rows)
    return len(rows)
