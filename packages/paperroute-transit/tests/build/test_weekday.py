"""Tests for the regular weekday trains of each run."""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import date, timedelta
from typing import Final

from paperroute_transit.build.weekday import REGULAR_DAYS, write_weekday_runs

MONDAY: Final[date] = date(2026, 10, 5)
KINDS: Final[tuple[str, ...]] = ('train',)
DEPARTURE: Final[int] = 8 * 3600  # seconds after midnight


def days_from(*, offsets: Sequence[int]) -> list[date]:
    """
    List the dates at offsets from a Monday.

    Parameters
    ----------
    offsets : Sequence[int]
        Days after the Monday.

    Returns
    -------
    list[date]
        The dates.
    """
    return [MONDAY + timedelta(days=offset) for offset in offsets]


@contextmanager
def timetable_of(
    *,
    trips: Sequence[tuple[int, str, int, list[date]]],
    kinds: dict[int, str] | None = None,
) -> Iterator[sqlite3.Connection]:
    """
    Build a timetable of runs and the trips that follow them.

    Parameters
    ----------
    trips : Sequence[tuple[int, str, int, list[date]]]
        Run id, route short name, first departure in seconds and the
        dates the trip runs on, of each trip.
    kinds : dict[int, str] | None
        Kind of each run id; every run is a train by default.

    Returns
    -------
    Iterator[sqlite3.Connection]
        An in-memory timetable, closed afterwards.
    """
    with closing(sqlite3.connect(':memory:')) as conn:
        yield from filled(conn=conn, trips=trips, kinds=kinds)


def filled(
    *,
    conn: sqlite3.Connection,
    trips: Sequence[tuple[int, str, int, list[date]]],
    kinds: dict[int, str] | None,
) -> Iterator[sqlite3.Connection]:
    """
    Fill a connection with the timetable and yield it.

    Parameters
    ----------
    conn : sqlite3.Connection
        An empty database.
    trips : Sequence[tuple[int, str, int, list[date]]]
        See `timetable_of`.
    kinds : dict[int, str] | None
        See `timetable_of`.

    Yields
    ------
    sqlite3.Connection
        The connection.
    """
    conn.executescript(
        """
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
          route TEXT NOT NULL, stations TEXT NOT NULL,
          trips INTEGER NOT NULL);
        CREATE TABLE trip_runs (trip_id TEXT PRIMARY KEY,
          run_id INTEGER NOT NULL);
        CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT,
          service_id TEXT);
        CREATE TABLE routes (route_id TEXT PRIMARY KEY, short_name TEXT);
        CREATE TABLE services (service_id TEXT, date TEXT);
        CREATE TABLE trip_skip (trip_id TEXT, date TEXT);
        CREATE TABLE stop_times (trip_id TEXT, seq INTEGER,
          dep INTEGER);
        """,
    )
    for run_id in {run_id for run_id, *_ in trips}:
        conn.execute(
            'INSERT INTO runs VALUES (?, ?, ?, ?, ?)',
            (run_id, (kinds or {}).get(run_id, 'train'), 'r', '[]', 1),
        )
    for number, (run_id, route, dep, dates) in enumerate(trips):
        trip_id = f't{number}'
        conn.execute(
            'INSERT INTO trip_runs VALUES (?, ?)', (trip_id, run_id),
        )
        conn.execute(
            'INSERT INTO trips VALUES (?, ?, ?)',
            (trip_id, f'route-{route}', f's{number}'),
        )
        conn.execute(
            'INSERT OR IGNORE INTO routes VALUES (?, ?)',
            (f'route-{route}', route),
        )
        conn.execute(
            'INSERT INTO stop_times VALUES (?, 1, ?)', (trip_id, dep),
        )
        conn.executemany(
            'INSERT INTO services VALUES (?, ?)',
            [(f's{number}', day.isoformat()) for day in dates],
        )
    yield conn


def trains_by_run(*, conn: sqlite3.Connection) -> dict[int, int]:
    """
    Read the weekday trains written.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.

    Returns
    -------
    dict[int, int]
        Trains by run id.
    """
    return dict(conn.execute('SELECT run_id, trains FROM weekday_runs'))


def test_a_train_on_three_weekdays_counts() -> None:
    """A departure that runs on three weekdays is a regular train."""
    trips = [
        (1, 'A', DEPARTURE, days_from(offsets=[0, 1, 2])),
        (1, 'A', DEPARTURE + 3600, days_from(offsets=[0, 1, 2, 3, 4])),
    ]
    with timetable_of(trips=trips) as conn:
        assert REGULAR_DAYS == 3
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 1
        assert trains_by_run(conn=conn) == {1: 2}


def test_a_one_day_special_counts_for_nothing() -> None:
    """A departure on fewer weekdays than the rule asks is not counted."""
    trips = [
        (1, 'A', DEPARTURE, days_from(offsets=[0, 1, 2, 3, 4, 7, 8])),
        (1, 'A', DEPARTURE + 3600, days_from(offsets=[2])),
        (2, 'A', DEPARTURE, days_from(offsets=[2, 3])),
    ]
    with timetable_of(trips=trips) as conn:
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 1
        assert trains_by_run(conn=conn) == {1: 1}


def test_weekends_do_not_count() -> None:
    """Saturdays and Sundays are not among the days counted."""
    trips = [(1, 'A', DEPARTURE, days_from(offsets=[0, 1, 5, 6, 12, 13]))]
    with timetable_of(trips=trips) as conn:
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 0
        assert trains_by_run(conn=conn) == {}


def test_a_timetable_of_two_weekdays_has_nothing_regular() -> None:
    """With fewer weekdays than the rule asks, no train is regular."""
    trips = [(1, 'A', DEPARTURE, days_from(offsets=[0, 1, 5, 6]))]
    with timetable_of(trips=trips) as conn:
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 0


def test_two_trips_with_one_key_on_a_day_count_once() -> None:
    """Trips of a run that share route and minute make one key."""
    trips = [
        (1, 'A', DEPARTURE, days_from(offsets=[0, 1])),
        (1, 'A', DEPARTURE + 30, days_from(offsets=[1, 2])),
        (1, 'A', DEPARTURE, days_from(offsets=[0])),
    ]
    with timetable_of(trips=trips) as conn:
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 1
        assert trains_by_run(conn=conn) == {1: 1}


def test_a_run_that_is_not_an_end_point_kind_gets_no_row() -> None:
    """Only the runs of the kinds given are counted."""
    trips = [
        (1, 'A', DEPARTURE, days_from(offsets=[0, 1, 2])),
        (2, 'A', DEPARTURE, days_from(offsets=[0, 1, 2])),
    ]
    with timetable_of(trips=trips, kinds={2: 'bus'}) as conn:
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 1
        assert trains_by_run(conn=conn) == {1: 1}


def test_a_run_with_no_regular_key_gets_no_row() -> None:
    """A run whose every key is irregular has no row, not a zero."""
    trips = [(1, 'A', DEPARTURE, days_from(offsets=[0, 1]))]
    with timetable_of(trips=trips) as conn:
        assert write_weekday_runs(conn=conn, kinds=KINDS) == 0
        assert trains_by_run(conn=conn) == {}
