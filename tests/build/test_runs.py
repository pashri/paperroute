"""Tests for following trips through their blocks."""

import dataclasses
import json
import shutil
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from paperroute.build.gtfs_files import Source
from paperroute.build.runs import (
    RUN_SCHEMA,
    RUN_STOPS_INDEX,
    RunReport,
    RunTable,
    TripCalls,
    block_trip_runs,
    carry_ons,
    chained,
    load_runs,
    next_on_block,
    next_trips,
    plain_trip_runs,
    trip_calls,
    trip_days,
    trip_skips,
)
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.model import RouteKind
from tests.build.feeds import regional_sources
from tests.build.network_feed import EAST_WEST, network_feed


@pytest.fixture(name='network_db', scope='module')
def fixture_network_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network feed into a timetable over a 3-day window.

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
    source = Source(name='sydneytrains',
                    archive=network_feed(path=folder / 'network.zip'))
    build_timetable(sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    return out


@pytest.fixture(name='network', scope='module')
def fixture_network(network_db: Path) -> Iterator[sqlite3.Connection]:
    """
    Open the network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.

    Yields
    ------
    sqlite3.Connection
        Open connection.
    """
    with closing(sqlite3.connect(network_db)) as conn:
        yield conn


@pytest.fixture(name='calls', scope='module')
def fixture_calls(network: sqlite3.Connection) -> list[TripCalls]:
    """
    Read every shown trip of the network.

    Parameters
    ----------
    network : sqlite3.Connection
        Network timetable.

    Returns
    -------
    list[TripCalls]
        Trips.
    """
    return list(trip_calls(conn=network))


def hand_trip(*, trip_id: str, route: str, stations: str, first_dep: int,
              last_arr: int, block: str | None = 'b',
              headsign: str | None = None) -> TripCalls:
    """
    Build a trip by hand.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    route : str
        Short name; T lines are rail, others buses.
    stations : str
        Space-separated station IDs.
    first_dep : int
        First departure, seconds.
    last_arr : int
        Last arrival, seconds.
    block : str | None
        Block ID.
    headsign : str | None
        Headsign; None for 'to <last station>'.

    Returns
    -------
    TripCalls
        Trip on service 'daily'.
    """
    rail = route.startswith('T')
    calls = tuple(stations.split())
    return TripCalls(
        trip_id=trip_id, route_id=f'r{route}',
        kind=RouteKind.TRAIN if rail else RouteKind.BUS, route=route,
        headsign=headsign or f'to {calls[-1]}', block=block, service='daily',
        first_dep=first_dep, last_arr=last_arr, stations=calls, rail=rail,
    )


def runs_of(*, trips: list[TripCalls]) -> dict[str, tuple[str, ...]]:
    """
    Chain trips with their next trips found once.

    Parameters
    ----------
    trips : list[TripCalls]
        Trips.

    Returns
    -------
    dict[str, tuple[str, ...]]
        Trip ID to run.
    """
    return chained(trips=trips, nexts=next_trips(trips=trips))


def carry_ons_of(
    *, trips: list[TripCalls],
) -> dict[str, tuple[str, str, str]]:
    """
    Find carry-ons with next trips found once.

    Parameters
    ----------
    trips : list[TripCalls]
        Trips.

    Returns
    -------
    dict[str, tuple[str, str, str]]
        Trip ID to the next trip's short name, headsign and trip ID.
    """
    return carry_ons(trips=trips, nexts=next_trips(trips=trips))


def test_trip_calls_merge_platforms_and_skip_hidden(
    calls: list[TripCalls],
) -> None:
    """Calls are at parent stations and hidden trips are not shown."""
    by_id = {trip.trip_id: trip for trip in calls}
    assert not by_id['empty1'].shown
    assert [trip.trip_id for trip in calls if not trip.shown] == ['empty1']
    assert 'empty1' not in runs_of(trips=calls)
    assert by_id['x1'].stations == ('E2', 'E1', 'C')
    assert by_id['cc1'].stations == ('C', 'L1', 'L2', 'J', 'C', 'E1', 'E2')
    assert by_id['v1'].stations == ('B1', 'B2')
    assert (by_id['x1'].first_dep, by_id['x1'].last_arr) == (29640, 30000)
    assert by_id['x1'].kind is RouteKind.TRAIN and by_id['x1'].rail
    assert by_id['v1'].kind is RouteKind.BUS and not by_id['v1'].rail
    assert by_id['x1'].stations[-1] is by_id['x2'].stations[0]


def test_trip_days_subtract_skipped_dates(
    network: sqlite3.Connection,
) -> None:
    """Services count their dates and trips their skipped dates."""
    network.execute("INSERT INTO trip_skip VALUES ('x1', '2026-10-06')")
    try:
        days = trip_days(conn=network)
        skips = trip_skips(conn=network)
    finally:
        network.rollback()
    assert days == {'sydneytrains:daily': 3}
    assert skips == {'x1': 1}


def test_chained_runs_through_a_block(calls: list[TripCalls]) -> None:
    """x1 carries on as x2 from where it ended."""
    assert runs_of(trips=calls)['x1'] == ('E2', 'E1', 'C', 'J', 'W1')


def test_chain_needs_the_next_trip_to_start_where_this_one_ended(
    calls: list[TripCalls],
) -> None:
    """z1 ends at E2 and z2 leaves R3, so they never join."""
    runs = runs_of(trips=calls)
    assert runs['z1'] == ('C', 'E1', 'E2')
    assert not any(('E2', 'R3') in pairwise(run) for run in runs.values())


def test_next_on_block_never_skips_the_next_trip() -> None:
    """A later trip from the right station is never chained past z2."""
    z1 = hand_trip(trip_id='z1', route='T4', stations='C E1 E2',
                   first_dep=0, last_arr=100)
    z2 = hand_trip(trip_id='z2', route='T4', stations='R3 R2 R1 J',
                   first_dep=200, last_arr=280)
    z3 = hand_trip(trip_id='z3', route='T4', stations='E2 E1 C',
                   first_dep=300, last_arr=400)
    assert next_on_block(trip=z1, later=[z1, z2, z3]) is None
    assert runs_of(trips=[z1, z2, z3])['z1'] == ('C', 'E1', 'E2')


def test_bus_blocks_chain_only_within_the_route(
    calls: list[TripCalls],
) -> None:
    """The 809 does not run on as the 811, but is tagged with it."""
    assert runs_of(trips=calls)['v1'] == ('B1', 'B2')
    assert carry_ons_of(trips=calls)['v1'] == (
        '811', 'Parramatta Stn', 'v2',
    )


def test_rail_blocks_chain_across_routes() -> None:
    """A T1 that carries on as a T9 from where it ended joins it."""
    t1 = hand_trip(trip_id='t1', route='T1', stations='A B C',
                   first_dep=0, last_arr=100)
    t9 = hand_trip(trip_id='t9', route='T9', stations='C D E',
                   first_dep=150, last_arr=250)
    assert next_on_block(trip=t1, later=[t1, t9]) is t9
    assert runs_of(trips=[t1, t9])['t1'] == ('A', 'B', 'C', 'D', 'E')


def test_carry_ons_tag_trains_too() -> None:
    """A T1 running on as a T9 is tagged; one running on as a T1 is not."""
    t1 = hand_trip(trip_id='t1', route='T1', stations='A B C',
                   first_dep=0, last_arr=100)
    t9 = hand_trip(trip_id='t9', route='T9', stations='C D E',
                   first_dep=150, last_arr=250)
    u1 = hand_trip(trip_id='u1', route='T1', stations='A B C',
                   first_dep=0, last_arr=100, block='c')
    u2 = hand_trip(trip_id='u2', route='T1', stations='C B A',
                   first_dep=150, last_arr=250, block='c')
    assert carry_ons_of(trips=[t1, t9, u1, u2]) == {
        't1': ('T9', 'to E', 't9'),
    }


def test_chained_keeps_the_return_until_a_hop_repeats(
    calls: list[TripCalls],
) -> None:
    """y1 runs on back east as y2, since no hop repeats."""
    assert runs_of(trips=calls)['y1'] == (
        'E2', 'E1', 'C', 'J', 'W1', 'W2', 'W1', 'J',
    )


def test_chained_cuts_a_shuttle_at_a_repeated_hop() -> None:
    """A block running back and forth stops before it repeats a hop."""
    shuttle = [
        hand_trip(trip_id=f's{n}', route='T1', stations=calls,
                  first_dep=n * 100, last_arr=n * 100 + 50)
        for n, calls in enumerate(['A B C', 'C B A', 'A B C'])
    ]
    assert runs_of(trips=shuttle)['s0'] == ('A', 'B', 'C', 'B', 'A')


def test_next_on_block_stops_at_a_hidden_trip() -> None:
    """An Empty Train between two revenue trips breaks the chain."""
    x1 = hand_trip(trip_id='x1', route='T1', stations='A B C',
                   first_dep=0, last_arr=100)
    empty = hand_trip(trip_id='e1', route='T1', stations='C D',
                      first_dep=150, last_arr=200, headsign='Empty Train')
    x3 = hand_trip(trip_id='x3', route='T9', stations='C B A',
                   first_dep=300, last_arr=400)
    trips = [x1, empty, x3]
    assert next_on_block(trip=x1, later=trips) is None
    assert runs_of(trips=trips)['x1'] == ('A', 'B', 'C')
    assert 'x1' not in carry_ons_of(trips=trips)
    kindless = dataclasses.replace(empty, kind=None, headsign='to D')
    assert next_on_block(trip=x1, later=[x1, kindless, x3]) is None


def run_id_of(*, conn: sqlite3.Connection, stations: str) -> int:
    """
    Find the run with the given stations.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with run tables.
    stations : str
        Space-separated station IDs.

    Returns
    -------
    int
        Run ID.
    """
    (run_id,), = conn.execute(
        'SELECT run_id FROM runs WHERE stations = ?',
        (json.dumps(stations.split()),),
    )
    return int(run_id)


def test_loop_run_keeps_its_second_call(network: sqlite3.Connection) -> None:
    """The T2 loop passes C twice, so C has two run_stops rows."""
    run_id = run_id_of(conn=network, stations='C L1 L2 J C E1 E2')
    assert network.execute(
        'SELECT kind, route FROM runs WHERE run_id = ?', (run_id,),
    ).fetchall() == [('train', 'T2')]
    assert network.execute(
        "SELECT pos FROM run_stops WHERE station_id = 'C' AND run_id = ?",
        (run_id,),
    ).fetchall() == [(0,), (4,)]


def test_load_runs_dedupes_and_weights(network: sqlite3.Connection) -> None:
    """Five plain T1 trips over three days share one run of 15 trip-days."""
    assert network.execute(
        'SELECT trips FROM runs WHERE stations = ?',
        (json.dumps(EAST_WEST.split()),),
    ).fetchall() == [(5 * 3,)]
    assert network.execute(
        'SELECT trip_id FROM trip_runs WHERE run_id = ?',
        (run_id_of(conn=network, stations='E2 E1 C J W1 W2 W1 J'),),
    ).fetchall() == [('y1',)]
    assert not network.execute(
        'SELECT 1 FROM runs WHERE stations = ?',
        (json.dumps(['C', 'J', 'R1']),),
    ).fetchall()
    shown = {
        trip_id
        for trip_id, headsign in network.execute(
            'SELECT trip_id, headsign FROM trips',
        )
        if headsign != 'Empty Train'
    }
    mapped = {
        trip_id
        for trip_id, in network.execute('SELECT trip_id FROM trip_runs')
    }
    assert mapped == shown
    assert 'empty1' not in mapped


def test_load_runs_subtracts_skipped_dates(
    network_db: Path, tmp_path: Path,
) -> None:
    """x1 skipped on one of three dates weighs 2 in its run."""
    db = tmp_path / 'timetable.db'
    shutil.copy(network_db, db)
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "INSERT INTO trip_skip VALUES ('x1', '2026-10-06');"
            'DROP TABLE runs; DROP TABLE run_stops; DROP TABLE trip_runs;'
            'DROP TABLE trip_next; DROP TABLE route_patterns;',
        )
        conn.executescript(RUN_SCHEMA)
        report = load_runs(conn=conn)
        run_id = run_id_of(conn=conn, stations='E2 E1 C J W1')
        assert conn.execute(
            'SELECT trips FROM runs WHERE run_id = ?', (run_id,),
        ).fetchall() == [(2,)]
    assert report == RunReport(runs=13, trips=27)


def test_trip_next_rows(network: sqlite3.Connection) -> None:
    """The 809 carries on as the 811; x1 carries on as the same T1."""
    assert network.execute(
        "SELECT route, headsign, next_trip FROM trip_next"
        " WHERE trip_id = 'v1'",
    ).fetchall() == [('811', 'Parramatta Stn', 'v2')]
    assert not network.execute(
        "SELECT 1 FROM trip_next WHERE trip_id = 'x1'",
    ).fetchall()


def test_run_stops_hold_positions(network: sqlite3.Connection) -> None:
    """C is the third station of the plain east-west run, found by index."""
    run_id = run_id_of(conn=network, stations=EAST_WEST)
    assert network.execute(
        "SELECT pos FROM run_stops WHERE station_id = 'C' AND run_id = ?",
        (run_id,),
    ).fetchall() == [(2,)]
    assert network.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'run_stops_station'",
    ).fetchall() == [(RUN_STOPS_INDEX,)]


def test_route_patterns_are_trip_level(network: sqlite3.Connection) -> None:
    """x1's pattern is its own stations, not its chained run."""
    rows = network.execute(
        'SELECT stations, trips FROM route_patterns '
        "WHERE route_id = 'sydneytrains:T1' AND headsign = 'C'",
    ).fetchall()
    assert rows == [(json.dumps(['E2', 'E1', 'C']), 1)]


def test_load_runs_skips_hidden_block_trips_and_orphans(
    network_db: Path, tmp_path: Path,
) -> None:
    """A hidden block trip has no run; unmatched trips and calls are skipped."""
    db = tmp_path / 'timetable.db'
    shutil.copy(network_db, db)
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "UPDATE trips SET block_id = 'b9' WHERE trip_id = 'empty1';"
            "DELETE FROM trips WHERE trip_id = 'cc1';"
            "DELETE FROM stop_times WHERE trip_id = 'cc2';"
            'DELETE FROM trip_runs;',
        )
        assert {'cc1', 'cc2'}.isdisjoint(
            trip.trip_id for trip in trip_calls(conn=conn)
        )
        plain = RunTable(days={'sydneytrains:daily': 3}, skips=Counter())
        blocks: list[TripCalls] = []
        assert 'empty1' not in dict(plain_trip_runs(
            trips=trip_calls(conn=conn), table=plain, blocks=blocks,
        ))
        assert 'empty1' in {trip.trip_id for trip in blocks}
        assert 'empty1' not in dict(block_trip_runs(
            trips=blocks, nexts=next_trips(trips=blocks), table=plain,
        ))


def test_merged_trainlink_train_gets_a_run(tmp_path: Path) -> None:
    """The feed's NRC trip, given the bundle's agency 711, still runs."""
    out = tmp_path / 'timetable.db'
    build_timetable(sources=regional_sources(tmp_path=tmp_path),
                    window=Window(start=date(2026, 10, 16), days=2), out=out)
    with closing(sqlite3.connect(out)) as conn:
        agency, runs = conn.execute(
            'SELECT t.agency_id, COUNT(r.run_id) FROM trips t '
            'LEFT JOIN trip_runs r USING (trip_id) '
            "WHERE t.trip_id = 'NT31-feed'",
        ).fetchone()
    assert (agency, runs) == ('711', 1)
