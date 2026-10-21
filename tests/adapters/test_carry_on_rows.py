"""Tests for departures that stay on as the vehicle becomes another trip."""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.carry_on_rows import MAX_HOPS
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.model import SYDNEY, CarryOn, Departure
from tests.build.network_feed import network_feed

DAY: Final[date] = date(2026, 10, 6)
CHAIN: Final[tuple[str, ...]] = (
    'E2', 'E1', 'C', 'J', 'W1', 'W2', 'R1', 'R2', 'R3',
)
START: Final[int] = 3 * 3600  # before any network trip
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
    first = START + k * HOP
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


def test_carry_on_arrives_on_the_service_day(chain_db: Path) -> None:
    """The arrival counts from the boarded trip's service day."""
    departure, = chain_departures(db=chain_db, dest='C')
    assert departure.service_day == DAY
    assert departure.departs == departure.arrives - timedelta(minutes=15)
