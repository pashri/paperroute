"""Tests for following trips through their blocks."""

import dataclasses
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from paperroute.build.gtfs_files import Source
from paperroute.build.runs import (
    TripCalls,
    carry_ons,
    chained,
    next_on_block,
    next_trips,
    trip_calls,
    trip_days,
    trip_skips,
)
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.model import RouteKind
from tests.build.network_feed import network_feed


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


def carry_ons_of(*, trips: list[TripCalls]) -> dict[str, tuple[str, str]]:
    """
    Find carry-ons with next trips found once.

    Parameters
    ----------
    trips : list[TripCalls]
        Trips.

    Returns
    -------
    dict[str, tuple[str, str]]
        Trip ID to the next trip's short name and headsign.
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
    """A trip with one skipped date over three runs on two of them."""
    network.execute("INSERT INTO trip_skip VALUES ('x1', '2026-10-06')")
    try:
        days = trip_days(conn=network)
        skips = trip_skips(conn=network)
    finally:
        network.rollback()
    assert days == {'sydneytrains:daily': 3}
    assert skips == {'x1': 1}
    assert days['sydneytrains:daily'] - skips['x1'] == 2


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
    assert carry_ons_of(trips=calls)['v1'] == ('811', 'Parramatta Stn')


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
    assert carry_ons_of(trips=[t1, t9, u1, u2]) == {'t1': ('T9', 'to E')}


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
