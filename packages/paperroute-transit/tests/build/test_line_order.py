"""Tests for each line's station order and branch blocks."""

import sqlite3
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from paperroute_testkit.paths import ROOT
from paperroute_transit.build.catalogue import route_sources
from paperroute_transit.build.line_order import (
    hop_graph,
    line_exits,
    line_order,
)
from paperroute_transit.build.line_stations import line_inputs
from paperroute_transit.core.model import STATION_FIRST

REAL_DB: Final[Path] = ROOT / 'data' / 'timetable.db'
RICHMOND: Final[str] = 'Richmond Station'
BLACKTOWN: Final[str] = 'Blacktown Station'
BLOCK_ENDS: Final[tuple[str, ...]] = (
    'Berowra Station', 'Emu Plains Station',
)


def northward(*, stations: str) -> dict[str, float]:
    """
    Give each station a latitude, the first furthest north.

    Parameters
    ----------
    stations : str
        Station IDs, space-separated, north to south.

    Returns
    -------
    dict[str, float]
        Latitude by station ID.
    """
    return {
        station: -33.0 - index / 100
        for index, station in enumerate(stations.split())
    }


def order_of(
    *, patterns: Sequence[tuple[str, int]], latitude: dict[str, float],
) -> list[tuple[str, str]]:
    """
    Run line_order on space-separated patterns.

    Parameters
    ----------
    patterns : Sequence[tuple[str, int]]
        Stations, space-separated, and trips of each pattern.
    latitude : dict[str, float]
        Latitude by station ID.

    Returns
    -------
    list[tuple[str, str]]
        Station and head rows.
    """
    return list(line_order(
        patterns=[(stations.split(), trips) for stations, trips in patterns],
        latitude=latitude,
    ))


def test_line_order_starts_at_the_northernmost_terminus() -> None:
    """A line run both ways opens at its northern end, heading itself."""
    rows = order_of(
        patterns=[('A B C D', 5), ('D C B A', 5)],
        latitude=northward(stations='A B C D'),
    )
    assert rows == [('A', 'A'), ('B', ''), ('C', ''), ('D', '')]


def test_line_order_branch_blocks_after_the_split() -> None:
    """Each way out of J opens a block, the busier way first."""
    rows = order_of(
        patterns=[('N M J W1 W2', 900), ('N M J R1 R2', 300)],
        latitude=northward(stations='N M J W1 R1 W2 R2'),
    )
    assert rows == [
        ('N', 'N'), ('M', ''), ('J', ''), ('W1', 'W2'), ('W2', ''),
        ('R1', 'R2'), ('R2', ''),
    ]


def test_line_order_drops_express_shortcuts() -> None:
    """An express hop A-D is no way of its own: no block opens."""
    rows = order_of(
        patterns=[('A B C D', 10), ('A D', 50)],
        latitude=northward(stations='A B C D'),
    )
    assert rows == [('A', 'A'), ('B', ''), ('C', ''), ('D', '')]


def test_line_order_keeps_a_loop_hop() -> None:
    """A loop pattern keeps J-C, C-L1 and C-E1, so every station shows."""
    rows = order_of(
        patterns=[('C L1 L2 J C E1 E2', 2)],
        latitude=northward(stations='L1 L2 J C E1 E2'),
    )
    assert sorted(station for station, _ in rows) == [
        'C', 'E1', 'E2', 'J', 'L1', 'L2',
    ]


def test_line_order_loop_lists_each_station_once() -> None:
    """A loop with a line back through it lists each station once."""
    rows = order_of(
        patterns=[('C L1 L2 J C E1 E2', 2), ('E2 E1 C J', 3)],
        latitude=northward(stations='L1 L2 J C E1 E2'),
    )
    stations = [station for station, _ in rows]
    assert Counter(stations) == Counter(['C', 'E1', 'E2', 'J', 'L1', 'L2'])


def test_line_order_without_a_terminus_starts_furthest_north() -> None:
    """A pure loop opens at its northernmost station."""
    rows = order_of(
        patterns=[('A B C A', 4)],
        latitude={'A': -33.9, 'B': -33.5, 'C': -33.7},
    )
    assert rows[0] == ('B', 'B')
    assert sorted(station for station, _ in rows) == ['A', 'B', 'C']


def test_line_order_lists_a_separate_part_too() -> None:
    """Stations no hop joins to the rest still appear, once each."""
    rows = order_of(
        patterns=[('A B', 4), ('X Y', 1)],
        latitude=northward(stations='A B X Y'),
    )
    assert rows == [('A', 'A'), ('B', ''), ('X', 'X'), ('Y', '')]


def test_line_order_drops_rare_hops() -> None:
    """A hop under RARE of the busiest one goes, and its lone station."""
    rows = order_of(
        patterns=[('A B C', 100), ('B X', 1)],
        latitude=northward(stations='A B C X'),
    )
    assert rows == [('A', 'A'), ('B', ''), ('C', '')]


def test_line_order_keeps_a_hop_only_rare_trips_skip() -> None:
    """A busy hop A-D stays when only rare trips call between A and D."""
    rows = order_of(
        patterns=[('A D', 100), ('A X D', 1)],
        latitude=northward(stations='A X D'),
    )
    assert rows == [('A', 'A'), ('D', '')]


def test_line_order_busiest_way_carries_on_the_enclosing_block() -> None:
    """At J the way to E carries on the block heading E; Q1 opens one."""
    rows = order_of(
        patterns=[
            ('N M A1 J P1 E', 500), ('N M B1 B2', 300),
            ('N M A1 J Q1', 200),
        ],
        latitude=northward(stations='N M A1 B1 J B2 P1 Q1 E'),
    )
    assert rows == [
        ('N', 'N'), ('M', ''), ('A1', 'E'), ('J', ''), ('P1', ''),
        ('E', ''), ('Q1', 'Q1'), ('B1', 'B2'), ('B2', ''),
    ]


@pytest.fixture(name='real', scope='module')
def real_fixture() -> Iterator[sqlite3.Connection]:
    """
    Open the real timetable when it has line_stations.

    Yields
    ------
    sqlite3.Connection
        Read-only connection.
    """
    if not REAL_DB.exists():
        pytest.skip('no real timetable')
    with closing(sqlite3.connect(f'{REAL_DB.as_uri()}?mode=ro',
                                 uri=True)) as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'",
        ).fetchone()
        if version is None or int(version[0]) < 4:
            pytest.skip('real timetable is older than schema 4')
        yield conn


def line_stops(*, conn: sqlite3.Connection) -> dict[str, set[str]]:
    """
    Gather the stations on the kept hops of every station-first line.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    dict[str, set[str]]
        Station IDs by line key.
    """
    keys = {
        source.route_id: f'{source.merge[0]}:{source.merge[1]}'
        for source in route_sources(conn=conn)
        if source.merge[0] in STATION_FIRST
    }
    inputs = line_inputs(conn=conn, keys=keys)
    return {
        key: set(hop_graph(patterns=patterns))
        for key, patterns in inputs.patterns.items()
    }


def line_rows(
    *, conn: sqlite3.Connection, key: str,
) -> list[tuple[str, str, str]]:
    """
    Read one line's rows with station names.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    key : str
        Line key.

    Returns
    -------
    list[tuple[str, str, str]]
        Station ID, station name and head name, in list order.
    """
    return conn.execute(
        'SELECT l.station_id, s.name, coalesce(h.name, l.head) '
        'FROM line_stations l JOIN stops s ON s.stop_id = l.station_id '
        'LEFT JOIN stops h ON h.stop_id = l.head '
        'WHERE l.line_key = ? ORDER BY l.pos', (key,),
    ).fetchall()


def test_real_t1_line_order(real: sqlite3.Connection) -> None:
    """T1 opens at Richmond; Berowra and Emu Plains blocks follow Blacktown."""
    rows = line_rows(conn=real, key='train:T1')
    names = [name for _, name, _ in rows]
    assert rows[0][1:] == (RICHMOND, RICHMOND)
    stops = line_stops(conn=real)['train:T1']
    assert Counter(station for station, _, _ in rows) == Counter(stops)
    heads = [index for index, (_, _, head) in enumerate(rows) if head]
    after = names.index(BLACKTOWN)
    for end in BLOCK_ENDS:
        assert any(
            rows[index][2] == end and index > after for index in heads
        ), end


def test_real_lines_list_each_station_once(real: sqlite3.Connection) -> None:
    """Every line lists each of its pattern stations exactly once."""
    stops = line_stops(conn=real)
    listed: dict[str, list[str]] = {}
    for key, station in real.execute(
        'SELECT line_key, station_id FROM line_stations ORDER BY pos',
    ):
        listed.setdefault(key, []).append(station)
    assert listed
    assert set(listed) == {key for key, found in stops.items() if found}
    for key, stations in listed.items():
        assert len(stations) == len(set(stations)), key
        assert set(stations) == stops[key], key


def test_real_line_stations_hold_only_station_first_lines(
    real: sqlite3.Connection,
) -> None:
    """Only train, metro, light rail and ferry lines get station lists."""
    kinds = {
        key.split(':')[0]
        for (key,) in real.execute(
            'SELECT DISTINCT line_key FROM line_stations',
        )
    }
    assert kinds == {str(kind) for kind in STATION_FIRST}


def test_line_exits_lists_routes_leaving_the_line() -> None:
    """Only hops from the line to a station off it are exits."""
    found = line_exits(
        line={'A', 'B', 'C'},
        patterns=[
            ('T9', 'A B X Y'.split()),
            ('CCN', 'A B X'.split()),
            ('T1', 'A B C'.split()),
            ('T3', 'X B C'.split()),
        ],
        sort={'T1': 1, 'T9': 9, 'CCN': 20, 'T3': 3},
    )
    assert found == {('B', 'X'): ('T9', 'CCN')}
