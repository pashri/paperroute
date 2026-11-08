"""Calendar facts from the timetable file."""

import sqlite3
from collections.abc import Callable, Collection, Sequence
from datetime import date, timedelta, tzinfo
from typing import Final

from paperroute_transit.core.model import Departure, ServiceSpan, service_time

type Fetch = Callable[[str], list[tuple[object, ...]]]
type FetchIn = Callable[[str, Collection[str]], list[tuple[object, ...]]]

HOLIDAYS: Final[str] = 'SELECT date FROM holidays'
HAS_HOLIDAYS: Final[str] = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holidays'"
)

SERVICE_DAY_LENGTH: Final[timedelta] = timedelta(hours=30)
TRIP_ENDS: Final[str] = """
SELECT MIN(first.dep), MAX(last.arr)
FROM (SELECT trip_id, MIN(seq) AS lo, MAX(seq) AS hi FROM stop_times
      WHERE trip_id IN ({marks}) GROUP BY trip_id) AS ends
JOIN stop_times AS first
  ON first.trip_id = ends.trip_id AND first.seq = ends.lo
JOIN stop_times AS last
  ON last.trip_id = ends.trip_id AND last.seq = ends.hi
"""


def service_span(
    *,
    departures: Sequence[Departure],
    day: date,
    fetch_in: FetchIn,
    zone: tzinfo,
) -> ServiceSpan | None:
    """
    Find when a pin's trips run on a service day.

    Parameters
    ----------
    departures : Sequence[Departure]
        The pin's departures around the day.
    day : date
        The service day.
    fetch_in : FetchIn
        Runs `TRIP_ENDS` for some trip IDs and returns its rows.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    ServiceSpan | None
        From the earliest first-stop departure to the latest last-stop
        arrival of the departures' trips on the day; None when there
        are none.
    """
    trips = {
        departure.trip_id
        for departure in departures
        if departure.service_day == day
    }
    if not trips:
        return None
    rows = fetch_in(TRIP_ENDS, trips)
    first = min(int(str(row[0])) for row in rows)
    last = max(int(str(row[1])) for row in rows)
    return ServiceSpan(
        first_start=service_time(day=day, seconds=first, zone=zone),
        last_end=service_time(day=day, seconds=last, zone=zone),
    )


def read_holidays(*, fetch: Fetch) -> frozenset[date]:
    """
    Read the public holidays the timetable records.

    Parameters
    ----------
    fetch : Fetch
        Runs one query against the timetable file and returns its rows.

    Returns
    -------
    frozenset[date]
        The holidays; empty when the file has no holidays table, including
        when the file is replaced by one without it between the queries.
    """
    try:
        if not fetch(HAS_HOLIDAYS):
            return frozenset()
        rows = fetch(HOLIDAYS)
    except sqlite3.OperationalError:
        return frozenset()
    return frozenset(date.fromisoformat(str(row[0])) for row in rows)


def connection_fetch(*, conn: sqlite3.Connection) -> Fetch:
    """
    Make a fetch that queries an open connection.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open timetable file.

    Returns
    -------
    Fetch
        Runs a query on conn.
    """
    return lambda sql: conn.execute(sql).fetchall()
