"""Tests for recording the roads buses arrive at a parent's kerbs by."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_transit.build.approach_roads import (
    APPROACH_ROAD_SCHEMA,
    load_approach_roads,
)
from paperroute_transit.core.names import NameRules
from tests.build.test_pairs import LAT, NORTH, add_stop, add_trip, kerb_db


@pytest.fixture(name='conn')
def _conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """
    Provide a timetable with parent P over kerb pa and two roads in.

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
        conn.executescript(APPROACH_ROAD_SCHEMA)
        add_stop(conn=conn, stop_id='P', lat=LAT)
        add_stop(conn=conn, stop_id='pa', lat=LAT, parent='P')
        add_stop(conn=conn, stop_id='m', lat=LAT + NORTH)
        add_stop(conn=conn, stop_id='w', lat=LAT - NORTH)
        conn.execute(
            "UPDATE stops SET name = 'Military Rd Opp Big Bear' "
            "WHERE stop_id = 'm'",
        )
        conn.execute(
            "UPDATE stops SET name = 'Wynyard Station' WHERE stop_id = 'w'",
        )
        yield conn


def test_load_approach_roads_names_the_street_before_the_kerb(
    conn: sqlite3.Connection,
) -> None:
    """The road is the previous stop's street, else its base name."""
    add_trip(conn=conn, trip_id='t0', route='6', way=0, stops=('m', 'pa'))
    add_trip(conn=conn, trip_id='t1', route='7', way=0, stops=('w', 'pa'))
    add_trip(conn=conn, trip_id='t2', route='8', way=0, stops=('pa', 'a'))
    assert load_approach_roads(conn=conn, rules=NameRules()) == 2
    assert list(
        conn.execute('SELECT * FROM approach_roads ORDER BY stop_id'),
    ) == [('P', 'm', 'Military Rd'), ('P', 'w', 'Wynyard')]


def test_load_approach_roads_skips_rail_and_parentless_stops(
    conn: sqlite3.Connection,
) -> None:
    """Rail trips and stops with no parent record nothing."""
    add_trip(
        conn=conn, trip_id='t0', route='T1', way=0, stops=('m', 'pa'), kind=2,
    )
    add_trip(conn=conn, trip_id='t1', route='6', way=0, stops=('m', 'a'))
    assert load_approach_roads(conn=conn, rules=NameRules()) == 0
