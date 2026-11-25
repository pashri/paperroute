"""Tests for recording the two kerbs of a parent station at build."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_transit.build.parent_kerbs import (
    PARENT_KERB_SCHEMA,
    facing_ways,
    kerb_onward,
    load_parent_kerbs,
    make_two_kerbs,
)
from tests.build.test_pairs import LAT, NORTH, add_stop, add_trip, kerb_db


@pytest.fixture(name='conn')
def _conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """
    Provide a timetable with parent P over kerbs pa and pb.

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
        conn.executescript(PARENT_KERB_SCHEMA)
        add_stop(conn=conn, stop_id='P', lat=LAT)
        add_stop(conn=conn, stop_id='pa', lat=LAT, parent='P')
        add_stop(conn=conn, stop_id='pb', lat=LAT + NORTH, parent='P')
        yield conn


def kerb_rows(*, conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    """
    Run the build and read the table.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    list[tuple[object, ...]]
        Rows by stop ID.
    """
    load_parent_kerbs(conn=conn)
    return list(conn.execute('SELECT * FROM parent_kerbs ORDER BY stop_id'))


def test_facing_ways_needs_two_different_ways() -> None:
    """Facing ways needs two different ways."""
    assert facing_ways(here=frozenset({'n'}), there=frozenset({'s'}))
    assert not facing_ways(here=frozenset({'n', 's'}), there=frozenset({'s'}))
    assert not facing_ways(here=frozenset(), there=frozenset({'s'}))


def test_load_parent_kerbs_keeps_kerbs_leaving_different_ways(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs keeps kerbs leaving different ways."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'))
    assert kerb_rows(conn=conn) == [
        ('pa', 'P', 'pb', '["end1"]', 1, 0),
        ('pb', 'P', 'pa', '["end2"]', 1, 0),
    ]


def far_pair(*, conn: sqlite3.Connection, metres: float) -> None:
    """
    Move kerb pb the given distance north of pa and give each a way.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    metres : float
        Distance between the kerbs.
    """
    conn.execute(
        'UPDATE stops SET lat = ? WHERE stop_id = ?',
        (LAT + metres / 111_195, 'pb'),
    )
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'))


def test_load_parent_kerbs_skips_kerbs_over_a_kilometre_apart(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips kerbs over a kilometre apart."""
    far_pair(conn=conn, metres=1200.0)
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_keeps_kerbs_a_few_hundred_metres_apart(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs keeps kerbs a few hundred metres apart."""
    far_pair(conn=conn, metres=300.0)
    assert len(kerb_rows(conn=conn)) == 2


def test_load_parent_kerbs_skips_kerbs_sharing_a_next_stop(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips kerbs sharing a next stop."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='7', way=0, stops=('pb', 'end1'))
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_reads_past_the_other_kerb(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs reads past the other kerb."""
    add_trip(
        conn=conn, trip_id='t0', route='6', way=0,
        stops=('pa', 'pb', 'end1'),
    )
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'))
    make_two_kerbs(conn=conn)
    onward, trips = kerb_onward(conn=conn)
    assert onward['pa'] == frozenset({'end1'})
    assert onward['pb'] == frozenset({'end1', 'end2'})
    assert trips == {'pa': 1, 'pb': 2}
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_skips_a_stop_passed_through(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips a stop passed through."""
    add_stop(conn=conn, stop_id='x', lat=LAT + 0.01)
    add_trip(
        conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'x', 'end1'),
    )
    conn.execute(
        "UPDATE stop_times SET pickup = 1, dropoff = 1 WHERE stop_id = 'x'"
    )
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'))
    assert [row[3] for row in kerb_rows(conn=conn)] == [
        '["end1"]', '["end2"]',
    ]


def test_load_parent_kerbs_skips_a_kerb_with_no_way_on(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips a kerb with no way on."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('end2', 'pb'))
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_skips_three_kerbs(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips three kerbs."""
    add_stop(conn=conn, stop_id='pc', lat=LAT - NORTH, parent='P')
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'))
    add_trip(conn=conn, trip_id='t2', route='8', way=0, stops=('pc', 'a'))
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_skips_a_parent_with_a_rail_stop(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips a parent with a rail stop."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(
        conn=conn, trip_id='t1', route='T1', way=1, stops=('pb', 'end2'),
        kind=2,
    )
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_ignores_trips_out_of_service(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs ignores trips out of service."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(
        conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'),
        service=0,
    )
    assert not kerb_rows(conn=conn)


def test_load_parent_kerbs_marks_a_busy_pair(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs marks a busy pair."""
    for n in range(4):
        add_trip(
            conn=conn, trip_id=f'a{n}', route='6', way=0,
            stops=('pa', 'end1'), dep=8 * 3600 + n * 600,
        )
        add_trip(
            conn=conn, trip_id=f'b{n}', route='6', way=1,
            stops=('pb', 'end2'), dep=8 * 3600 + n * 600,
        )
    assert load_parent_kerbs(conn=conn) == 1
    assert list(conn.execute(
        'SELECT stop_id, trips, busy FROM parent_kerbs ORDER BY stop_id',
    )) == [('pa', 4, 1), ('pb', 4, 1)]


def test_load_parent_kerbs_skips_a_kerb_with_no_coordinates(
    conn: sqlite3.Connection,
) -> None:
    """Load parent kerbs skips a parent whose kerb has no position."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('pa', 'end1'))
    add_trip(conn=conn, trip_id='t1', route='6', way=1, stops=('pb', 'end2'))
    conn.execute(
        'UPDATE stops SET lat = NULL, lon = NULL WHERE stop_id = ?', ('pb',),
    )
    assert not kerb_rows(conn=conn)
