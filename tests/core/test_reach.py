"""Tests for the reach builder: sides, the main line and indented spurs."""

import random
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.core.geo import Point
from paperroute.core.model import RouteKind, StopPlace
from paperroute.core.reach import (
    Reach,
    ReachInput,
    RowKind,
    RunTail,
    StripKey,
    build_reach,
)

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'
TOWN_HALL: Final[str] = '200070'
STRATHFIELD: Final[str] = '213510'
HORNSBY_LINE: Final[tuple[str, ...]] = (
    'Asquith Station',
    'Berowra Station',
    'Mount Colah Station',
    'Mount Kuring-gai Station',
)
STATIONS: Final[tuple[str, ...]] = tuple(f'S{k}' for k in range(12))


def tail(
    after: str, trips: int, before: str = '',
) -> RunTail:
    """
    Make a tail from space-separated station IDs.

    Parameters
    ----------
    after : str
        Stations after the From.
    trips : int
        Trip-days.
    before : str
        Stations before the From.

    Returns
    -------
    RunTail
        The tail.
    """
    return RunTail(
        before=tuple(before.split()), after=tuple(after.split()), trips=trips,
    )


def reach_of(
    *,
    tails: Sequence[RunTail],
    points: Mapping[str, Point] | None = None,
    names: Mapping[str, str] | None = None,
) -> Reach:
    """
    Build the reach from C, naming each station by its ID.

    Parameters
    ----------
    tails : Sequence[RunTail]
        Tails through C.
    points : Mapping[str, Point] | None
        Station positions in metres.
    names : Mapping[str, str] | None
        Station names other than their IDs.

    Returns
    -------
    Reach
        The reach.
    """
    keys = {'C'}.union(*(t.before + t.after for t in tails))
    places = {
        key: StopPlace(
            stop_id=key,
            name=(names or {}).get(key, key),
            point=(points or {}).get(key),
        )
        for key in keys
    }
    return build_reach(data=ReachInput(
        origin=places['C'], tails=tuple(tails), places=places,
        station_of={key: key for key in keys},
    ))


def ids(*, reach: Reach) -> list[str]:
    """
    List a reach's station IDs from top to bottom.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    list[str]
        Station IDs.
    """
    return [row.place.stop_id for row in reach.rows]


def row_of(*, reach: Reach, key: str) -> int:
    """
    Find a station's row.

    Parameters
    ----------
    reach : Reach
        The reach.
    key : str
        Station ID.

    Returns
    -------
    int
        Row index.
    """
    return ids(reach=reach).index(key)


def origin_row(*, reach: Reach) -> int:
    """
    Find the ORIGIN row.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    int
        Row index.
    """
    return next(
        index
        for index, row in enumerate(reach.rows)
        if row.kind == RowKind.ORIGIN
    )


def parents_on_their_side(*, reach: Reach) -> bool:
    """
    Say whether every row's track parent is on its side or is ORIGIN.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    bool
        True when no track crosses FROM.
    """
    return all(
        reach.rows[row.parent].kind == RowKind.ORIGIN
        or reach.rows[row.parent].above == row.above
        for row in reach.rows
        if row.parent is not None
    )


def track_names(*, reach: Reach, index: int) -> list[str]:
    """
    Name the stations a row's track passes on its way back to FROM.

    Parameters
    ----------
    reach : Reach
        The reach.
    index : int
        Row index.

    Returns
    -------
    list[str]
        Station names from the row to FROM.
    """
    found = []
    at: int | None = index
    while at is not None:
        found.append(reach.rows[at].place.name)
        at = reach.rows[at].parent
    return found


def test_line_through_from_puts_bigger_side_on_top() -> None:
    """The side holding the R spur goes on top, the spur indented."""
    reach = reach_of(tails=[
        tail('J W1 W2', 6), tail('J R1 R2 R3', 3), tail('E1 E2', 8),
    ])
    assert ids(reach=reach) == [
        'W2', 'W1', 'R3', 'R2', 'R1', 'J', 'C', 'E1', 'E2',
    ]
    assert [reach.rows[row_of(reach=reach, key=key)].depth for key in (
        'R1', 'R2', 'R3', 'J', 'W1',
    )] == [1, 1, 1, 0, 0]
    assert reach.rows[row_of(reach=reach, key='R1')].parent == row_of(
        reach=reach, key='J',
    )
    assert reach.rows[row_of(reach=reach, key='W2')].parent == row_of(
        reach=reach, key='W1',
    )
    assert reach.rows[row_of(reach=reach, key='J')].parent == 6
    assert reach.rows[6].kind == RowKind.ORIGIN
    assert reach.rows[6].parent is None
    assert reach.stations == 8


def test_side_with_more_spur_rows_goes_on_top() -> None:
    """Spur rows outrank stations when choosing the top side."""
    reach = reach_of(tails=[
        tail('J W1', 6), tail('J R1', 3), tail('E1 E2 E3 E4', 8),
    ])
    assert ids(reach=reach) == [
        'W1', 'R1', 'J', 'C', 'E1', 'E2', 'E3', 'E4',
    ]


def test_side_with_more_stations_goes_on_top() -> None:
    """With equal spur rows, the longer side goes on top."""
    reach = reach_of(tails=[tail('W1 W2 W3', 6), tail('A1 A2', 8)])
    assert ids(reach=reach) == ['W3', 'W2', 'W1', 'C', 'A1', 'A2']


def test_station_joins_the_way_most_trips_come_from() -> None:
    """Z follows B1's way: more trips reach it from B1's stations."""
    reach = reach_of(tails=[
        tail('A1 Z', 5), tail('B1 Z', 3), tail('B1 B2 Z', 3),
    ])
    rows = {row.place.stop_id: row for row in reach.rows}
    assert rows['Z'].above == rows['B1'].above != rows['A1'].above
    assert parents_on_their_side(reach=reach)


def test_run_entering_from_another_way_joins_at_its_way() -> None:
    """S, reached from A2 by its own way, hangs at A2, never at FROM."""
    reach = reach_of(tails=[
        tail('A1 A2 M', 20), tail('B1 S', 12), tail('A1 A2 S', 7),
        tail('A1 A2 S X', 6), tail('B1 B2', 30),
    ])
    assert ids(reach=reach) == ['M', 'X', 'S', 'A2', 'A1', 'C', 'B1', 'B2']
    assert reach.rows[2].depth == 1
    assert reach.rows[2].parent == row_of(reach=reach, key='A2')


def test_busier_branch_is_main_line() -> None:
    """At J the branch with more trips is the main line."""
    west = reach_of(tails=[tail('J W1', 6), tail('J R1', 3)])
    south = reach_of(tails=[tail('J W1', 3), tail('J R1', 6)])
    assert ids(reach=west) == ['C', 'J', 'R1', 'W1']
    assert [row.kind for row in west.rows] == [
        RowKind.ORIGIN, RowKind.MAIN, RowKind.BRANCH, RowKind.MAIN,
    ]
    assert ids(reach=south) == ['C', 'J', 'W1', 'R1']


def test_tie_goes_to_further_branch_then_name() -> None:
    """Equal trips go to the further branch, then the lower name."""
    tails = [tail('J A1 A2', 3), tail('J B1', 3)]
    points: dict[str, Point] = {
        'C': (0, 0), 'J': (0, 1000), 'A1': (0, 2000), 'A2': (0, 9000),
        'B1': (0, 5000),
    }
    names = {'A1': 'Zed', 'B1': 'Alpha'}
    further = reach_of(tails=tails, points=points, names=names)
    assert ids(reach=further) == ['C', 'J', 'B1', 'A1', 'A2']
    same = {**points, 'A2': (0, 5000)}
    named = reach_of(tails=tails, points=same, names=names)
    assert ids(reach=named) == ['C', 'J', 'A1', 'A2', 'B1']


def test_spur_inside_spur_indents_twice() -> None:
    """A spur off a spur is two levels in, after its junction."""
    reach = reach_of(tails=[
        tail('J W1 W2', 6), tail('J R1 R2', 3), tail('J R1 S1', 2),
    ])
    assert ids(reach=reach) == ['C', 'J', 'R1', 'S1', 'R2', 'W1', 'W2']
    assert [row.depth for row in reach.rows] == [0, 0, 1, 2, 1, 0, 0]
    assert reach.rows[3].parent == 2
    assert reach.rows[4].parent == 2


def test_detour_is_listed_like_a_spur() -> None:
    """A path that rejoins later hangs at its junction, its end once."""
    reach = reach_of(tails=[tail('J A B K', 5), tail('J D K', 3)])
    assert ids(reach=reach) == ['C', 'J', 'D', 'A', 'B', 'K']
    assert reach.rows[2].depth == 1
    assert reach.rows[2].parent == 1


def test_third_way_hangs_at_from_on_top() -> None:
    """A third way out of FROM is a depth-1 spur at FROM, on top."""
    reach = reach_of(tails=[
        tail('E1 E2', 6), tail('W1 W2', 5), tail('N1 N2', 2),
    ])
    assert ids(reach=reach) == ['E2', 'E1', 'N2', 'N1', 'C', 'W1', 'W2']
    assert [row.depth for row in reach.rows] == [0, 0, 1, 1, 0, 0, 0]
    assert reach.rows[3].parent == 4
    assert reach.rows[2].parent == 3
    assert reach.rows[1].parent == 4
    assert all(row.above for row in reach.rows[:4])


def test_loop_through_from_splits_into_two_sides() -> None:
    """A loop back through FROM's line gives two sides, each once."""
    reach = build_reach(data=loop_input())
    home = origin_row(reach=reach)
    above = {row.place.stop_id for row in reach.rows[:home]}
    below = {row.place.stop_id for row in reach.rows[home + 1:]}
    assert {frozenset(above), frozenset(below)} == {
        frozenset({'C', 'E1', 'E2'}), frozenset({'L2', 'L1'}),
    }
    assert len(reach.rows) == 6
    assert parents_on_their_side(reach=reach)


def loop_input() -> ReachInput:
    """
    Make the tails from J of a line that loops back through it.

    Returns
    -------
    ReachInput
        Tails `C E1 E2` (6 trips) and `L2 L1 C E1 E2` (2 trips).
    """
    keys = ('J', 'C', 'E1', 'E2', 'L1', 'L2')
    places = {
        key: StopPlace(stop_id=key, name=key, point=None) for key in keys
    }
    return ReachInput(
        origin=places['J'],
        tails=(tail('C E1 E2', 6), tail('L2 L1 C E1 E2', 2)),
        places=places,
        station_of={key: key for key in keys},
    )


def test_one_sided_reach_gets_two_approach_rows() -> None:
    """One side goes below, the two stations before FROM above."""
    reach = reach_of(tails=[tail('E1 E2', 4, before='P3 P2 P1')])
    assert ids(reach=reach) == ['P2', 'P1', 'C', 'E1', 'E2']
    assert [row.kind for row in reach.rows[:2]] == [RowKind.APPROACH] * 2
    assert [row.parent for row in reach.rows] == [1, 2, None, 2, 3]
    assert reach.stations == 2


def test_approach_rows_skip_placed_stations() -> None:
    """A station before FROM that is also after it is not an approach."""
    reach = reach_of(tails=[
        tail('E1 E2', 5, before='P2 X'), tail('E1 X', 2),
    ])
    assert ids(reach=reach) == ['P2', 'C', 'E1', 'X', 'E2']
    assert reach.rows[0].kind == RowKind.APPROACH
    assert reach.rows[0].parent == 1


def test_terminus_tail_only_gives_approach_rows() -> None:
    """A tail ending at FROM adds no stations, only approach rows."""
    reach = reach_of(tails=[
        tail('', 9, before='P2 P1'), tail('E1', 3, before='Q1'),
    ])
    assert ids(reach=reach) == ['P2', 'P1', 'C', 'E1']
    assert reach.stations == 1


def test_no_tails_is_an_empty_reach() -> None:
    """With no tails the reach is FROM alone, with no stations."""
    reach = reach_of(tails=[])
    assert ids(reach=reach) == ['C']
    assert reach.rows[0].kind == RowKind.ORIGIN
    assert reach.stations == 0
    assert reach.station_of == {'C': 'C'}


def test_station_reached_through_another_way_moves_to_it() -> None:
    """A station its way only reaches through another way is moved."""
    reach = build_reach(data=stray_input())
    home = origin_row(reach=reach)
    below = [row.place.stop_id for row in reach.rows[home + 1:]]
    above = [row.place.stop_id for row in reach.rows[:home]]
    assert sorted([above, below]) == [['B1', 'Y'], ['X', 'A1']]
    assert parents_on_their_side(reach=reach)


def stray_input() -> ReachInput:
    """
    Make tails where Y follows A1 mostly, but only ever after B1.

    Returns
    -------
    ReachInput
        Tails `A1 X` (9 trips), `B1 A1 Y` (2) and `B1 Y` (1) from C.
    """
    keys = ('C', 'A1', 'B1', 'X', 'Y')
    places = {
        key: StopPlace(stop_id=key, name=key, point=None) for key in keys
    }
    return ReachInput(
        origin=places['C'],
        tails=(tail('A1 X', 9), tail('B1 A1 Y', 2), tail('B1 Y', 1)),
        places=places,
        station_of={key: key for key in keys},
    )


@st.composite
def random_tails(draw: st.DrawFn) -> list[RunTail]:
    """
    Draw tails over 12 stations, each calling at a station once.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    list[RunTail]
        Tails from C.
    """
    stations = st.sampled_from(STATIONS)
    return draw(st.lists(
        st.builds(
            RunTail,
            before=st.lists(
                st.one_of(stations, st.just('C')), max_size=4,
            ).map(tuple),
            after=st.lists(stations, unique=True, max_size=8).map(tuple),
            trips=st.integers(min_value=1, max_value=9),
        ),
        max_size=8,
    ))


@given(tails=random_tails())
def test_each_station_once(tails: list[RunTail]) -> None:
    """Every station is on one row, and each track steps in at most one."""
    points: dict[str, Point] = {
        key: (index * 700.0, (index % 3) * 900.0)
        for index, key in enumerate(('C', *STATIONS))
    }
    reach = reach_of(tails=tails, points=points)
    keys = ids(reach=reach)
    after = {key for each in tails for key in each.after}
    approach = [
        row.place.stop_id
        for row in reach.rows
        if row.kind == RowKind.APPROACH
    ]
    assert len(keys) == len(set(keys))
    assert set(keys) == after | set(approach) | {'C'}
    assert [row.kind for row in reach.rows].count(RowKind.ORIGIN) == 1
    assert reach.stations == len(after)
    assert parents_on_their_side(reach=reach)
    firsts = {each.after[0] for each in tails if each.after}
    assert all(
        row.place.stop_id in firsts
        for row in reach.rows
        if row.parent is not None
        and reach.rows[row.parent].kind == RowKind.ORIGIN
        and row.kind != RowKind.APPROACH
    )
    for row in reach.rows:
        if row.parent is not None:
            parent = reach.rows[row.parent]
            assert parent.depth in (row.depth, row.depth - 1)
            assert row.depth > 0 or parent.depth == 0


def test_build_reach_is_fast_for_a_big_station() -> None:
    """2,000 tails of 40 stations over 300 build in well under 5 s."""
    chance = random.Random(6)
    keys = [f'S{index}' for index in range(300)]
    tails = [
        RunTail(
            before=(),
            after=tuple(chance.sample(keys, 40)),
            trips=chance.randint(1, 20),
        )
        for _ in range(2000)
    ]
    points: dict[str, Point] = {
        key: (chance.uniform(0, 1e5), chance.uniform(0, 1e5))
        for key in keys
    }
    start = time.perf_counter()
    reach = reach_of(tails=tails, points=points)
    assert time.perf_counter() - start < 5
    assert reach.stations == 300


def has_runs(*, path: Path) -> bool:
    """
    Say whether a timetable file exists and holds the run tables.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    bool
        True when the runs table is there.
    """
    if not path.exists():
        return False
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'runs'",
        ).fetchall()
    return bool(found)


def train_reach(*, station: str) -> tuple[Reach, set[str]]:
    """
    Build a station's Train strip reach from the real timetable.

    Parameters
    ----------
    station : str
        Station ID.

    Returns
    -------
    tuple[Reach, set[str]]
        The reach, and every station after the From in its tails.
    """
    with closing(SqliteNetworkRepository(path=REAL_DB)) as repo:
        found = repo.reach_input(
            key=StripKey(station=station, kind=RouteKind.TRAIN, routes=()),
            origin=StopPlace(stop_id=station, name=station, point=None),
        )
    assert found is not None
    after = {key for each in found.tails for key in each.after}
    return build_reach(data=found), after


@pytest.mark.skipif(
    not has_runs(path=REAL_DB),
    reason='needs data/timetable.db with run tables',
)
def test_town_hall_train_strip_has_two_sides_and_strands_nothing() -> None:
    """Town Hall reaches both ways, every track on its own side."""
    reach, after = train_reach(station=TOWN_HALL)
    home = origin_row(reach=reach)
    assert reach.stations == len(after)
    assert any(row.kind != RowKind.APPROACH for row in reach.rows[:home])
    assert reach.rows[home + 1:]
    assert parents_on_their_side(reach=reach)


@pytest.mark.skipif(
    not has_runs(path=REAL_DB),
    reason='needs data/timetable.db with run tables',
)
def test_strathfield_keeps_the_hornsby_branch_together() -> None:
    """The stations past Hornsby hang on the Hornsby branch."""
    reach, _ = train_reach(station=STRATHFIELD)
    names = [row.place.name for row in reach.rows]
    for name in HORNSBY_LINE:
        track = track_names(reach=reach, index=names.index(name))
        assert 'Hornsby Station' in track
