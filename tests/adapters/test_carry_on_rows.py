"""Tests for departures that stay on as the vehicle becomes another trip."""

import dataclasses
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.carry_on_rows import (
    CARRY_ONS,
    MAX_HOPS,
    first_to_arrive,
)
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.model import SYDNEY, CarryOn, Departure, TripFacts
from tests.build.network_feed import network_feed
from tests.core.factories import START, departure

DAY: Final[date] = date(2026, 10, 6)
CHAIN: Final[tuple[str, ...]] = (
    'E2', 'E1', 'C', 'J', 'W1', 'W2', 'R1', 'R2', 'R3',
)
FIRST: Final[int] = 3 * 3600  # before any network trip
HOP: Final[int] = 600  # seconds per chain trip


@pytest.fixture(name='network_db', scope='module')
def _network_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network feed over a 3-day window.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file.
    """
    folder = tmp_path_factory.mktemp('network')
    out = folder / 'timetable.db'
    build_timetable(
        sources=[
            Source(
                name='sydneytrains',
                archive=network_feed(path=folder / 'network.zip'),
            ),
        ],
        window=Window(start=date(2026, 10, 5), days=3),
        out=out,
    )
    return out


@pytest.fixture(name='chain_db')
def _chain_db(network_db: Path, tmp_path: Path) -> Path:
    """
    Copy the network and add a chain of 8 joined trips, c1 to c8.

    Trip ck runs from the k-th to the (k+1)-th station of CHAIN, at the
    _1 platforms, early on every day. c1 is a T1, the rest T2.

    Parameters
    ----------
    network_db : Path
        Network timetable.
    tmp_path : Path
        Working directory.

    Returns
    -------
    Path
        Changed copy.
    """
    db = tmp_path / 'timetable.db'
    shutil.copy(network_db, db)
    with closing(sqlite3.connect(db)) as conn:
        for k in range(8):
            add_trip(conn=conn, k=k)
        conn.executemany(
            'INSERT INTO trip_joins VALUES (?, ?)',
            [(f'c{k}', f'c{k + 1}') for k in range(1, 8)],
        )
        conn.commit()
    return db


def add_trip(*, conn: sqlite3.Connection, k: int) -> None:
    """
    Insert chain trip c(k + 1) and its two calls.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    k : int
        Position of the trip in the chain, from 0.
    """
    trip = f'c{k + 1}'
    route = 'T1' if k == 0 else 'T2'
    conn.execute(
        'INSERT INTO trips VALUES (?, ?, ?, ?, 0, ?, NULL, ?, NULL, 1)',
        (
            trip, f'sydneytrains:{route}', 'sydneytrains:daily',
            CHAIN[k + 1], 'chain', 'sydneytrains',
        ),
    )
    first = FIRST + k * HOP
    conn.executemany(
        'INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 0, 0)',
        [
            (trip, 1, f'{CHAIN[k]}_1', first, first),
            (trip, 2, f'{CHAIN[k + 1]}_1', first + 300, first + 300),
        ],
    )


def change(*, db: Path, sql: str) -> None:
    """
    Run SQL statements against a timetable file.

    Parameters
    ----------
    db : Path
        Timetable.
    sql : str
        Statements.
    """
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(sql)


def chain_departures(*, db: Path, dest: str) -> list[Departure]:
    """
    List the chain's departures from E2 to a station on DAY.

    Parameters
    ----------
    db : Path
        Timetable.
    dest : str
        Destination station.

    Returns
    -------
    list[Departure]
        Departures of chain trips, early in the morning.
    """
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.on_day(
            ends=('E2', dest),
            day=DAY,
            start=datetime(2026, 10, 6, 2, 0, tzinfo=SYDNEY),
            end=datetime(2026, 10, 6, 4, 0, tzinfo=SYDNEY),
        )
    return [d for d in found if d.trip_id.startswith('c')]


@pytest.fixture(name='to_c')
def _to_c(chain_db: Path) -> Iterator[list[Departure]]:
    """
    The chain's departures from E2 to C.

    Parameters
    ----------
    chain_db : Path
        Timetable with the chain.

    Yields
    ------
    list[Departure]
        Departures.
    """
    yield chain_departures(db=chain_db, dest='C')


def test_carry_on_departure_listed(to_c: list[Departure]) -> None:
    """E2 to C stays on c1 as c2: c1's departure, c2's arrival."""
    departure, = to_c
    assert departure.trip_id == 'c1'
    assert departure.route == 'T1'
    assert departure.facts.onto == CarryOn(
        route='T2', headsign='C', trip_id='c2',
    )
    assert departure.facts.then is None
    assert departure.destination_stop == 'E1_1'
    assert departure.destination_seq == 2
    assert departure.arrives == datetime(
        2026, 10, 6, 3, 15, tzinfo=SYDNEY,
    )


def test_direct_trip_not_listed_twice(chain_db: Path) -> None:
    """E2 to E1 is c1 itself, listed once without onto."""
    departure, = chain_departures(db=chain_db, dest='E1')
    assert (departure.trip_id, departure.facts.onto) == ('c1', None)


def test_carry_on_respects_one_way(chain_db: Path) -> None:
    """C pickup-only on c2 gives nothing."""
    change(
        db=chain_db,
        sql="UPDATE stop_times SET dropoff = 1 WHERE trip_id = 'c2' "
        'AND seq = 2',
    )
    assert not chain_departures(db=chain_db, dest='C')


@pytest.mark.parametrize('sql', [
    "INSERT INTO trip_skip VALUES ('c2', '2026-10-06')",
    "UPDATE trips SET service_id = 'other' WHERE trip_id = 'c2'",
])
def test_carry_on_skips_next_trip_not_running(
    chain_db: Path, sql: str,
) -> None:
    """c2 skipped on the day, or on another service, gives nothing."""
    change(db=chain_db, sql=sql)
    assert not chain_departures(db=chain_db, dest='C')


def test_carry_on_two_hops(chain_db: Path) -> None:
    """J is on c3, the third trip of the chain."""
    departure, = chain_departures(db=chain_db, dest='J')
    assert departure.facts.onto is not None
    assert departure.facts.onto.trip_id == 'c3'
    assert departure.arrives == datetime(
        2026, 10, 6, 3, 25, tzinfo=SYDNEY,
    )


def test_carry_on_stops_at_max_hops(chain_db: Path) -> None:
    """R2 on c7 is MAX_HOPS joins on; R3 on c8 is one too many."""
    assert MAX_HOPS == 6
    departure, = chain_departures(db=chain_db, dest='R2')
    assert departure.facts.onto is not None
    assert departure.facts.onto.trip_id == 'c7'
    assert not chain_departures(db=chain_db, dest='R3')


def test_carry_on_not_across_service_zero(chain_db: Path) -> None:
    """A next trip out of service gives nothing."""
    change(
        db=chain_db,
        sql="UPDATE trips SET service = 0 WHERE trip_id = 'c2'",
    )
    assert not chain_departures(db=chain_db, dest='C')


def test_carry_on_arrival_must_follow_the_boarded_departure(
    chain_db: Path,
) -> None:
    """A To arrival at or before the boarding time gives nothing."""
    change(
        db=chain_db,
        sql=f'UPDATE stop_times SET arr = {FIRST}, dep = {FIRST} '
        "WHERE trip_id = 'c2' AND seq = 2",
    )
    assert not chain_departures(db=chain_db, dest='C')


def test_carry_on_arrives_on_the_service_day(chain_db: Path) -> None:
    """The arrival counts from the boarded trip's service day."""
    departure, = chain_departures(db=chain_db, dest='C')
    assert departure.service_day == DAY
    assert departure.departs == departure.arrives - timedelta(minutes=15)


def test_first_to_arrive_drops_beaten_carry_ons() -> None:
    """A carry-on stays only when no later direct trip gets there first."""
    onto = TripFacts(onto=CarryOn(route='T2', headsign='C'))
    slow = dataclasses.replace(
        departure(departs=START, ride=60, trip_id='slow'), facts=onto,
    )
    quick = dataclasses.replace(
        departure(departs=START, ride=20, trip_id='quick'), facts=onto,
    )
    later = departure(departs=START + timedelta(minutes=5), ride=30)
    earlier = departure(departs=START - timedelta(minutes=5), ride=5)
    found = first_to_arrive(departures=[slow, quick, later, earlier])
    assert [d.trip_id for d in found] == [
        'quick', later.trip_id, earlier.trip_id,
    ]


def add_loop(*, db: Path, trip: str, calls: str) -> None:
    """
    Add a C_2 platform and a daily trip calling at C twice.

    Parameters
    ----------
    db : Path
        Timetable.
    trip : str
        Trip ID.
    calls : str
        SQL ``VALUES`` rows of (seq, stop, minutes after FIRST, pickup,
        dropoff).
    """
    change(db=db, sql=f"""
        INSERT INTO stops SELECT 'C_2', name, 'C', lat, lon, '2'
          FROM stops WHERE stop_id = 'C_1';
        INSERT INTO trips VALUES ('{trip}', 'sydneytrains:T2',
          'sydneytrains:daily', 'C', 0, 'loop', NULL, 'sydneytrains',
          NULL, 1);
        WITH c(seq, stop, minutes, pickup, dropoff) AS (VALUES {calls})
        INSERT INTO stop_times SELECT '{trip}', seq, stop,
          {FIRST} + minutes * 60, {FIRST} + minutes * 60, pickup, dropoff
          FROM c;
    """)


def test_carry_on_counts_stops_once_at_the_join(
    to_c: list[Departure],
) -> None:
    """E2 to C on c1 then c2 stops at E1 and C: E1 is not counted twice."""
    departure, = to_c
    assert departure.stops == 2


def test_carry_on_counts_stops_over_many_hops(chain_db: Path) -> None:
    """E2 to W2 stays on through four joins and stops five times."""
    departure, = chain_departures(db=chain_db, dest='W2')
    assert departure.stops == 5


def test_direct_row_uses_first_destination_call(chain_db: Path) -> None:
    """A trip reaching C twice arrives, and counts stops, at the first C."""
    add_loop(db=chain_db, trip='cloop', calls="""
        (1, 'E2_1', 0, 0, 0), (2, 'E1_1', 5, 0, 0), (3, 'W1_1', 7, 1, 1),
        (4, 'C_1', 10, 0, 0), (5, 'J_1', 15, 0, 0), (6, 'C_2', 20, 0, 0)
    """)
    loop, = [d for d in chain_departures(db=chain_db, dest='C')
             if d.trip_id == 'cloop']
    assert (loop.destination_stop, loop.stops) == ('C_1', 2)
    assert loop.arrives == datetime(2026, 10, 6, 3, 10, tzinfo=SYDNEY)


def test_stop_count_skips_another_from_platform(chain_db: Path) -> None:
    """A trip moving to a second E2 platform first doesn't count it."""
    change(db=chain_db, sql="""
        INSERT INTO stops SELECT 'E2_2', name, 'E2', lat, lon, '2'
          FROM stops WHERE stop_id = 'E2_1';
    """)
    add_loop(db=chain_db, trip='cplat', calls="""
        (1, 'E2_1', 0, 0, 0), (2, 'E2_2', 2, 0, 0), (3, 'E1_1', 5, 0, 0),
        (4, 'C_1', 10, 0, 0)
    """)
    found = [d for d in chain_departures(db=chain_db, dest='C')
             if d.trip_id == 'cplat']
    assert [d.stops for d in found] == [2]


def test_carry_on_uses_first_destination_call(chain_db: Path) -> None:
    """Staying on into a trip reaching C twice arrives at the first C."""
    add_loop(db=chain_db, trip='cloop', calls="""
        (1, 'E1_1', 5, 0, 0), (2, 'C_1', 10, 0, 0),
        (3, 'J_1', 15, 0, 0), (4, 'C_2', 20, 0, 0)
    """)
    change(db=chain_db, sql="""
        DELETE FROM trip_joins;
        INSERT INTO trip_joins VALUES ('c1', 'cloop');
    """)
    departure, = chain_departures(db=chain_db, dest='C')
    assert departure.facts.onto is not None
    assert departure.facts.onto.trip_id == 'cloop'
    assert departure.stops == 2
    assert departure.arrives == datetime(2026, 10, 6, 3, 10, tzinfo=SYDNEY)


def test_patterns_name_the_trip_joined_onto(chain_db: Path) -> None:
    """A chain trip's pattern names the next; the last names none."""
    with closing(SqliteTimetableRepository(path=chain_db)) as repo:
        found = repo.patterns(trip_ids=['c1', 'c8'], around=DAY)
    assert (found['c1'].next_trip, found['c8'].next_trip) == ('c2', None)


def test_carry_ons_reach_destination_through_the_chain(
    chain_db: Path,
) -> None:
    """Destination calls are found per chained trip, not by stop."""
    with closing(SqliteTimetableRepository(path=chain_db)) as repo:
        sql = CARRY_ONS.format(
            shape=repo.shape_column(), service=repo.service_filter(),
        )
        plan = repo.fetch(
            sql='EXPLAIN QUERY PLAN ' + sql,
            params={'day': '', 'origin': '', 'dest': '', 'lo': 0, 'hi': 0},
        )
    assert not [
        row
        for row in plan
        if 'SEARCH d USING INDEX stop_times_stop' in str(row)
    ]


def test_leg_lines_follow_the_trip_a_vehicle_becomes(chain_db: Path) -> None:
    """E2 to C stays on c1 as c2, so both lines ride the leg."""
    with closing(SqliteTimetableRepository(path=chain_db)) as repo:
        found = repo.leg_lines(ends=('E2', 'C'), day=date(2026, 10, 6))
    assert found == {'sydneytrains:T1', 'sydneytrains:T2'}

