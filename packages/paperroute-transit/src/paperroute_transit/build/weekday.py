"""Count each run's regular weekday trains."""

import json
import sqlite3
from collections.abc import Collection
from typing import Final

REGULAR_DAYS: Final[int] = 3  # weekdays
WEEKDAY_SCHEMA: Final[str] = """
CREATE TABLE weekday_runs (run_id INTEGER PRIMARY KEY,
  trains INTEGER NOT NULL);
"""
WEEKDAY_RUNS: Final[str] = """
INSERT INTO weekday_runs (run_id, trains)
SELECT run_id, COUNT(*) FROM (
  SELECT tr.run_id, r.short_name,
    (SELECT st.dep FROM stop_times st WHERE st.trip_id = t.trip_id
      ORDER BY st.seq LIMIT 1) / 60 AS minute
  FROM runs ru
  JOIN trip_runs tr ON tr.run_id = ru.run_id
  JOIN trips t ON t.trip_id = tr.trip_id
  JOIN routes r ON r.route_id = t.route_id
  JOIN services sv ON sv.service_id = t.service_id
  WHERE ru.kind IN (SELECT value FROM json_each(:kinds))
    AND strftime('%w', sv.date) BETWEEN '1' AND '5'
    AND NOT EXISTS (SELECT 1 FROM trip_skip sk
      WHERE sk.trip_id = t.trip_id AND sk.date = sv.date)
  GROUP BY tr.run_id, r.short_name, minute
  HAVING COUNT(DISTINCT sv.date) >= :days
)
GROUP BY run_id
"""


def write_weekday_runs(
    *, conn: sqlite3.Connection, kinds: Collection[str],
) -> int:
    """
    Create and fill ``weekday_runs``.

    A trip's key is its route short name, its run and its first
    departure minute. A key is regular when it runs on at least
    ``REGULAR_DAYS`` weekdays of the timetable, skipped dates excluded.
    Two trips with one key on one day make one key. A run with no
    regular key has no row.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the run tables. The caller commits.
    kinds : Collection[str]
        Run kinds to count.

    Returns
    -------
    int
        Number of runs written.
    """
    conn.executescript(WEEKDAY_SCHEMA)
    done = conn.execute(
        WEEKDAY_RUNS,
        {'kinds': json.dumps(sorted(kinds)), 'days': REGULAR_DAYS},
    )
    return done.rowcount
