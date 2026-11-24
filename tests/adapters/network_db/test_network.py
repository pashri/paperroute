"""Tests for network db: network."""

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.build.passed import PASSED_SCHEMA
from paperroute_transit.build.weekday import WEEKDAY_SCHEMA
from paperroute_transit.core.chooser import Level
from paperroute_transit.core.reach import ReachInput, StripKey
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun
from tests.adapters.network_db.helpers import (
    TRAIN,
    opened,
    place,
    set_runs,
    tails_from,
)


def add_hops(
    *, path: Path, hops: Sequence[tuple[str, str, Sequence[str]]],
    trains: Sequence[tuple[int, int]],
) -> None:
    """
    Fill rail_hops, track_places and weekday_runs.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    hops : Sequence[tuple[str, str, Sequence[str]]]
        Both stations and the passed stations of each inside hop.
    trains : Sequence[tuple[int, int]]
        Run ID and weekday trains of each run with a row.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            'DROP TABLE IF EXISTS rail_hops;'
            'DROP TABLE IF EXISTS weekday_runs;'
            'DROP TABLE IF EXISTS track_places;'
            + PASSED_SCHEMA + WEEKDAY_SCHEMA,
        )
        conn.executemany(
            "INSERT INTO rail_hops VALUES (?, ?, ?, 1, NULL)",
            [(a, b, json.dumps(list(p))) for a, b, p in hops],
        )
        conn.executemany(
            'INSERT INTO weekday_runs VALUES (?, ?)', trains,
        )
        conn.execute(
            "INSERT INTO track_places VALUES ('osm:5', 'Halt', -33.9, 151.1)",
        )
        conn.commit()


def reach_for(
    *, path: Path, kind: SydneyMode, station: str = 'C',
) -> ReachInput:
    """
    Read the reach input of a Sydney strip.

    Parameters
    ----------
    path : Path
        Timetable file.
    kind : SydneyMode
        Strip kind.
    station : str
        From station ID.

    Returns
    -------
    ReachInput
        The reach input.
    """
    with closing(SqliteNetworkRepository(
        path=path, city=SYDNEY,
    )) as repo:
        found = repo.reach_input(
            key=StripKey(station=station, kind=kind, routes=()),
            origin=place(stop_id=station),
        )
    assert found is not None
    return found


def test_reach_input_loads_the_train_network(copy: Path) -> None:
    """A train strip carries the hops and weekday runs of its runs."""
    set_runs(path=copy, runs=[('A C D', 3), ('C E', 3), ('C F', 3)])
    add_hops(
        path=copy,
        hops=[
            ('A', 'C', ()), ('C', 'D', ['osm:5']), ('C', 'E', []),
            ('X', 'Y', ['Z']),
        ],
        trains=[(1, 8), (2, 6)],
    )
    network = reach_for(path=copy, kind=SydneyMode.TRAIN).network
    assert network is not None
    assert network.hops[('C', 'D')] == Hop(passed=('osm:5',), inside=True)
    assert set(network.hops) == {('A', 'C'), ('C', 'D'), ('C', 'E')}
    assert network.runs == (
        WeekdayRun(route='T1', stations=('A', 'C', 'D'), trains=8),
        WeekdayRun(route='T1', stations=('C', 'E'), trains=6),
    )


def test_reach_input_loads_only_runs_that_stop_at_the_from(
    copy: Path,
) -> None:
    """A run that passes the From without stopping is not loaded."""
    set_runs(path=copy, runs=[('W2 C E1', 3), ('W2 ~C E1 E2', 3)])
    add_hops(
        path=copy,
        hops=[('W2', 'C', ()), ('C', 'E1', ()), ('E1', 'E2', ())],
        trains=[(1, 40), (2, 60)],
    )
    network = reach_for(
        path=copy, kind=SydneyMode.TRAIN, station='C',
    ).network
    assert network is not None
    assert network.runs == (
        WeekdayRun(route='T1', stations=('W2', 'C', 'E1'), trains=40),
    )
    assert set(network.hops) == {('W2', 'C'), ('C', 'E1')}


def test_reach_input_without_run_calls_loads_passing_runs(
    copy: Path,
) -> None:
    """A file with no run_calls cannot tell a pass from a stop."""
    set_runs(path=copy, runs=[('W2 ~C E1', 3)])
    add_hops(
        path=copy, hops=[('W2', 'C', ()), ('C', 'E1', ())],
        trains=[(1, 40)],
    )
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE run_calls')
        conn.commit()
    network = reach_for(
        path=copy, kind=SydneyMode.TRAIN, station='C',
    ).network
    assert network is not None
    assert len(network.runs) == 1


def test_train_to_column_offers_only_stations_a_stopping_run_calls_at(
    copy: Path,
) -> None:
    """A station only a passing run calls at is not offered."""
    set_runs(path=copy, runs=[('W2 C E1', 3), ('W2 ~C E1 E2', 3)])
    with closing(SqliteNetworkRepository(
        path=copy, city=SYDNEY,
    )) as repo:
        column = repo.choices(path=(*TRAIN, (Level.STATION, 'C')))
    assert column is not None
    assert [c.key for c in column.items[1:]] == ['E1']


def test_reach_input_names_the_places_of_passed_stations(
    copy: Path,
) -> None:
    """A track station that a hop passes has a place."""
    set_runs(path=copy, runs=[('C D', 3)])
    add_hops(path=copy, hops=[('C', 'D', ['osm:5'])], trains=[(1, 8)])
    found = reach_for(path=copy, kind=SydneyMode.TRAIN)
    assert found.places['osm:5'].name == 'Halt'


def test_reach_input_has_no_network_for_a_bus(copy: Path) -> None:
    """A kind without end points keeps today's reach input."""
    add_hops(path=copy, hops=[('B2', 'B3', [])], trains=[])
    found = reach_for(path=copy, kind=SydneyMode.BUS, station='B2')
    assert found.network is None


def test_reach_input_has_no_network_for_a_metro(copy: Path) -> None:
    """A line kind keeps today's reach input."""
    add_hops(path=copy, hops=[('C', 'D', [])], trains=[])
    found = reach_for(path=copy, kind=SydneyMode.METRO)
    assert found.network is None


def test_reach_input_without_track_tables_has_an_empty_network(
    copy: Path,
) -> None:
    """A file with no hop tables gives a network with nothing in it."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.executescript(
            'DROP TABLE IF EXISTS rail_hops;'
            'DROP TABLE IF EXISTS weekday_runs;',
        )
    found = reach_for(path=copy, kind=SydneyMode.TRAIN)
    assert found.network == TrainNetwork(hops={}, runs=())


def drop_track_tables(*, path: Path) -> None:
    """
    Drop the tables a file built before the Strip's train kinds lacks.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        for table in ('track_places', 'rail_hops', 'weekday_runs'):
            conn.execute(f'DROP TABLE {table}')
        conn.commit()


def test_reach_input_for_train_reads_an_older_file(copy: Path) -> None:
    """A file without the track tables gives an empty train network."""
    drop_track_tables(path=copy)
    with closing(SqliteNetworkRepository(
        path=copy, city=SYDNEY,
    )) as repo:
        found = repo.reach_input(
            key=StripKey(station='C', kind=SydneyMode.TRAIN, routes=()),
            origin=place(stop_id='C'),
        )
    assert found is not None
    assert found.network == TrainNetwork(hops={}, runs=())
    assert found.tails


def test_reach_input_for_bus_reads_an_older_file(copy: Path) -> None:
    """A bus pin gives the same input with or without the track tables."""
    key = StripKey(
        station='B2', kind=SydneyMode.BUS, routes=('811',),
    )
    with opened(path=copy) as repo:
        before = repo.reach_input(key=key, origin=place(stop_id='B2'))
    drop_track_tables(path=copy)
    with opened(path=copy) as repo:
        after = repo.reach_input(key=key, origin=place(stop_id='B2'))
    assert after == before


def test_reach_input_reads_the_kerb_across(network_db: Path) -> None:
    """A both-sides key reads the kerb across's runs, mapped to the From."""
    with opened(path=network_db) as network:
        found = network.reach_input(
            key=StripKey(
                station='B2', kind=SydneyMode.BUS, routes=(), across='B1',
            ),
            origin=place(stop_id='B2'),
        )
    across = tails_from(path=network_db, station='B1', kind=SydneyMode.BUS)
    assert found is not None and across
    assert found.across_tails == across
    assert found.station_of['B1'] == 'B2'
    assert {*across[0].after} <= found.places.keys()


def test_reach_input_reads_a_kerb_across_under_a_station(
    network_db: Path,
) -> None:
    """A kerb across named by its platform reads its station's runs."""
    with opened(path=network_db) as network:
        found = network.reach_input(
            key=StripKey(
                station='R3', kind=SydneyMode.TRAIN, routes=(),
                across='W2_1',
            ),
            origin=place(stop_id='R3'),
        )
    across = tails_from(path=network_db, station='W2')
    assert found is not None and across
    assert found.across_tails == across
    assert 'W1' in found.places
    assert found.station_of['W2'] == 'R3'
