"""Tests for pairing opposite kerbs at timetable build."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.pairs import (
    PAIR_SCHEMA,
    load_pairs,
    reference_day,
)
from paperroute_transit.build.timetable import create_schema

DAY: Final[str] = '2026-11-24'  # a Tuesday
LAT: Final[float] = -33.9
NORTH: Final[float] = 0.00036  # degrees, about 40 m
FAR: Final[float] = 0.0015  # degrees, about 167 m


def kerb_db(*, path: Path) -> sqlite3.Connection:
    """
    Build an empty timetable with the pairs table and stop `a`.

    Parameters
    ----------
    path : Path
        Database file.

    Returns
    -------
    sqlite3.Connection
        Open connection.
    """
    conn = sqlite3.connect(path)
    create_schema(conn=conn)
    conn.executescript(PAIR_SCHEMA)
    conn.execute("INSERT INTO services VALUES ('wk', ?)", (DAY,))
    add_stop(conn=conn, stop_id='end1', lat=LAT + 0.05)
    add_stop(conn=conn, stop_id='end2', lat=LAT - 0.05)
    add_stop(conn=conn, stop_id='a', lat=LAT)
    return conn


@pytest.fixture(name='conn')
def _conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """
    Provide a timetable with stop `a`, closed afterwards.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Yields
    ------
    sqlite3.Connection
        Open connection.
    """
    with closing(kerb_db(path=tmp_path / 't.db')) as conn:
        yield conn


def add_stop(
    *, conn: sqlite3.Connection, stop_id: str, lat: float,
    parent: str | None = None,
) -> None:
    """
    Insert a stop.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    stop_id : str
        Stop ID, also its name.
    lat : float
        Latitude.
    parent : str | None
        Parent station.
    """
    conn.execute(
        'INSERT INTO stops VALUES (?, ?, ?, ?, ?, ?)',
        (stop_id, stop_id, parent, lat, 151.2, ''),
    )


def add_trip(
    *, conn: sqlite3.Connection, trip_id: str, route: str, way: int,
    stops: tuple[str, ...], dep: int = 8 * 3600, kind: int = 3,
) -> None:
    """
    Insert a trip calling at the stops a minute apart.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip_id : str
        Trip ID.
    route : str
        Route ID and short name.
    way : int
        Direction.
    stops : tuple[str, ...]
        Stops in order.
    dep : int
        First departure, seconds after midnight.
    kind : int
        GTFS route type.
    """
    conn.execute(
        'INSERT OR IGNORE INTO routes VALUES (?, ?, ?, ?, NULL, NULL, '
        "NULL, 'test')",
        (route, route, '', kind),
    )
    conn.execute(
        "INSERT INTO trips VALUES (?, ?, 'wk', 'x', ?, NULL, NULL, "
        "'test', NULL, 1)",
        (trip_id, route, way),
    )
    conn.executemany(
        'INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 0, 0, NULL)',
        [
            (trip_id, seq, stop, dep + seq * 60, dep + seq * 60)
            for seq, stop in enumerate(stops)
        ],
    )


def pairs_of(*, conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """
    Run the pairing and read the table.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    list[tuple[str, str, int]]
        Rows, sorted.
    """
    load_pairs(conn=conn, projection=SYDNEY_PROJECTION)
    return sorted(conn.execute('SELECT * FROM stop_pairs'))


def test_load_pairs_joins_opposite_kerbs(conn: sqlite3.Connection) -> None:
    """Stops on opposite ways of one route pair both ways round."""
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('a', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('b', 'end2'))
    assert pairs_of(conn=conn) == [('a', 'b', 0), ('b', 'a', 0)]


def test_load_pairs_leaves_two_candidates_single(
    conn: sqlite3.Connection,
) -> None:
    """A stop with two candidates pairs with neither."""
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_stop(conn=conn, stop_id='c', lat=LAT - NORTH)
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('a', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('b', 'end2'))
    add_trip(conn=conn, trip_id='t2', route='6', way=1, stops=('c', 'end2'))
    assert pairs_of(conn=conn) == []


def test_load_pairs_needs_opposite_ways(conn: sqlite3.Connection) -> None:
    """Two stops on the same way of a route do not pair."""
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('a', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=0, stops=('b', 'end1'))
    assert pairs_of(conn=conn) == []


def test_load_pairs_skips_a_kerb_served_both_ways(
    conn: sqlite3.Connection,
) -> None:
    """A stop that serves a route both ways is not paired."""
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('a', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('a', 'end2'))
    add_trip(conn=conn, trip_id='t2', route='6', way=1, stops=('b', 'end2'))
    assert pairs_of(conn=conn) == []


def test_load_pairs_keeps_railway_stations_out(
    conn: sqlite3.Connection,
) -> None:
    """A place a rail route serves never pairs."""
    add_stop(conn=conn, stop_id='st', lat=LAT)
    add_stop(conn=conn, stop_id='p1', lat=LAT, parent='st')
    add_stop(conn=conn, stop_id='p2', lat=LAT, parent='st')
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_trip(
        conn=conn, trip_id='t0', route='T1', way=0, stops=('p1', 'end1'),
        kind=2,
    )
    add_trip(conn=conn, trip_id='t1', route='6', way=0, stops=('p2', 'end1'))
    add_trip(conn=conn, trip_id='t2', route='6', way=1, stops=('b', 'end2'))
    assert pairs_of(conn=conn) == []


def test_load_pairs_pairs_parent_groups(conn: sqlite3.Connection) -> None:
    """Parent groups pair, not their child stops."""
    add_stop(conn=conn, stop_id='G1', lat=LAT)
    add_stop(conn=conn, stop_id='G2', lat=LAT + NORTH)
    add_stop(conn=conn, stop_id='k1', lat=LAT, parent='G1')
    add_stop(conn=conn, stop_id='k2', lat=LAT + NORTH, parent='G2')
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('k1', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('k2', 'end2'))
    assert pairs_of(conn=conn) == [('G1', 'G2', 0), ('G2', 'G1', 0)]


def test_load_pairs_stays_within_its_radius(conn: sqlite3.Connection) -> None:
    """Stops further apart than the radius do not pair."""
    add_stop(conn=conn, stop_id='b', lat=LAT + FAR)
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('a', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('b', 'end2'))
    assert pairs_of(conn=conn) == []


def add_hourly(
    *, conn: sqlite3.Connection, stop: str, way: int, count: int,
) -> None:
    """
    Add trips leaving a stop ten minutes apart from 8:00.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    stop : str
        Stop the trips leave.
    way : int
        Direction.
    count : int
        Number of trips.
    """
    for index in range(count):
        add_trip(
            conn=conn, trip_id=f'{stop}{index}', route='6', way=way,
            stops=(stop,), dep=8 * 3600 + index * 600,
        )


def test_load_pairs_marks_a_busy_pair(conn: sqlite3.Connection) -> None:
    """Six departures an hour at each kerb make the pair busy."""
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_hourly(conn=conn, stop='a', way=0, count=6)
    add_hourly(conn=conn, stop='b', way=1, count=6)
    assert pairs_of(conn=conn) == [('a', 'b', 1), ('b', 'a', 1)]


def test_load_pairs_needs_both_kerbs_busy(conn: sqlite3.Connection) -> None:
    """A pair is busy only when both kerbs are."""
    add_stop(conn=conn, stop_id='b', lat=LAT + NORTH)
    add_hourly(conn=conn, stop='a', way=0, count=6)
    add_hourly(conn=conn, stop='b', way=1, count=5)
    assert pairs_of(conn=conn) == [('a', 'b', 0), ('b', 'a', 0)]


def test_reference_day_skips_weekends_and_holidays(
    conn: sqlite3.Connection,
) -> None:
    """The reference day is the first weekday that is no holiday."""
    conn.executemany(
        "INSERT INTO services VALUES ('wk', ?)",
        [('2026-11-21',), ('2026-11-23',)],
    )
    conn.execute("INSERT INTO holidays VALUES ('2026-11-23')")
    assert reference_day(conn=conn) == DAY
