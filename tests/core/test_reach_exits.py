"""Tests for exit rows: the ways trips leave a strip's line."""

from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.reach_exits import (
    exit_leads,
    exit_name,
    found_exits,
    grouped_exits,
)
from paperroute_testkit.core.reach_factories import (
    ids,
    random_tails,
    reach_of,
    tail,
)
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import Reach, RowKind, RunTail
from paperroute_transit.core.reach_layout import LineExit

LINE: Final[frozenset[str]] = frozenset({'C', 'J', 'E1'})
OFF_IDS: Final[tuple[str, ...]] = tuple(f'X{k}' for k in range(6))
MOST_EXITS: Final[int] = 3  # per junction in the random reaches


def way_off(junction: str, off: str, trips: int) -> LineExit:
    """
    Make an exit taken by T9 trains.

    Parameters
    ----------
    junction : str
        Station the trips leave the line at.
    off : str
        First station off the line.
    trips : int
        Trip-days making the hop.

    Returns
    -------
    LineExit
        The exit.
    """
    return LineExit(junction=junction, off=off, lines=('T9',), trips=trips)


def row_at(*, reach: Reach, key: str) -> int:
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


def test_found_exits_from_tails() -> None:
    """Each tail's first station off the line follows its junction."""
    found = found_exits(
        tails=[
            tail('J W1 W2', 5), tail('J R1', 3), tail('L1 L2 J', 2),
        ],
        line=LINE,
        origin='C',
    )
    assert found == {('J', 'W1'): 5, ('J', 'R1'): 3, ('C', 'L1'): 2}


def test_found_exits_keeps_the_busier_junction() -> None:
    """One off station after two junctions keeps the busier one."""
    busier = found_exits(
        tails=[tail('J X', 5), tail('E1 X', 3)], line=LINE, origin='C',
    )
    tied = found_exits(
        tails=[tail('J X', 3), tail('J E1 X', 3)], line=LINE, origin='C',
    )
    assert busier == {('J', 'X'): 5}
    assert tied == {('E1', 'X'): 3}


def test_found_exits_ignore_tails_on_the_line() -> None:
    """A tail that stays on the line has no exit."""
    assert not found_exits(
        tails=[tail('J E1', 4, before='P1')], line=LINE, origin='C',
    )


def test_exit_rows_follow_the_junctions_branches() -> None:
    """J, then its branch, then its exit, then the line on from J."""
    reach = reach_of(
        tails=[tail('J E1 E2', 6), tail('J B1 B2', 2)],
        exits=[LineExit(junction='J', off='X1', lines=('T9', 'CCN'),
                        trips=4)],
    )
    assert ids(reach=reach) == ['C', 'J', 'B1', 'B2', 'X1', 'E1', 'E2']
    exit_row = reach.rows[row_at(reach=reach, key='X1')]
    junction = row_at(reach=reach, key='J')
    assert exit_row.kind is RowKind.EXIT
    assert exit_row.depth == reach.rows[junction].depth
    assert exit_row.parent == junction
    assert exit_row.trips == 4
    assert exit_row.lines == ('T9', 'CCN')
    assert all(not row.lines for row in reach.rows if row is not exit_row)


def test_exit_rows_above_sit_above_the_junction() -> None:
    """Above the From: the line on, the exit, the branch, then J."""
    reach = reach_of(
        tails=[tail('J E1 E2', 6), tail('J B1 B2', 2), tail('S1 S2', 1)],
        exits=[way_off('J', 'X1', 4)],
    )
    assert ids(reach=reach) == [
        'E2', 'E1', 'X1', 'B2', 'B1', 'J', 'C', 'S1', 'S2',
    ]
    exit_row = reach.rows[2]
    assert exit_row.kind is RowKind.EXIT
    assert exit_row.above
    assert exit_row.parent == row_at(reach=reach, key='J')


def test_three_exits_at_one_junction() -> None:
    """Three exits are three rows, busiest nearest the junction."""
    reach = reach_of(
        tails=[tail('J E1', 6)],
        exits=[
            way_off('J', 'X3', 2), way_off('J', 'X1', 5),
            way_off('J', 'X2', 3),
        ],
    )
    assert ids(reach=reach) == ['C', 'J', 'X1', 'X2', 'X3', 'E1']
    junction = row_at(reach=reach, key='J')
    assert [row.parent for row in reach.rows[2:5]] == [junction] * 3
    assert [row.kind for row in reach.rows[2:5]] == [RowKind.EXIT] * 3
    assert reach.stations == 2


def test_exit_at_the_from_goes_below() -> None:
    """The From's exit is the first row below it."""
    reach = reach_of(
        tails=[tail('E1 E2', 5), tail('W1 W2', 4)],
        exits=[way_off('C', 'X1', 3)],
    )
    assert ids(reach=reach) == ['E2', 'E1', 'C', 'X1', 'W1', 'W2']
    assert reach.rows[3].kind is RowKind.EXIT
    assert reach.rows[3].parent == 2


def test_exits_at_the_from_alone() -> None:
    """With every tail leaving at the From, only its exits are below."""
    bare = reach_of(tails=[tail('', 5)], exits=[way_off('C', 'X1', 5)])
    led = reach_of(
        tails=[tail('', 5, before='P2 P1')], exits=[way_off('C', 'X1', 5)],
    )
    assert ids(reach=bare) == ['C', 'X1']
    assert bare.rows[1].kind is RowKind.EXIT
    assert ids(reach=led) == ['P2', 'P1', 'C', 'X1']
    assert led.rows[0].kind is RowKind.APPROACH


@st.composite
def tails_with_exits(
    draw: st.DrawFn,
) -> tuple[list[RunTail], list[LineExit]]:
    """
    Draw random tails and up to three exits at each junction.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    tuple[list[RunTail], list[LineExit]]
        Tails from C, and exits each with its own off station.
    """
    tails = draw(random_tails())
    junctions = ['C', *sorted({key for each in tails for key in each.after})]
    offs = draw(st.lists(st.sampled_from(OFF_IDS), unique=True, max_size=6))
    exits: list[LineExit] = []
    for off in offs:
        junction = draw(st.sampled_from(junctions))
        if sum(each.junction == junction for each in exits) < MOST_EXITS:
            trips = draw(st.integers(min_value=1, max_value=9))
            exits.append(way_off(junction, off, trips))
    return tails, exits


@given(drawn=tails_with_exits())
def test_random_reaches_with_exits_list_each_station_once(
    drawn: tuple[list[RunTail], list[LineExit]],
) -> None:
    """Every off station is one exit row hung from its junction."""
    tails, exits = drawn
    reach = reach_of(tails=tails, exits=exits)
    keys = ids(reach=reach)
    assert len(keys) == len(set(keys))
    for each in exits:
        row = reach.rows[keys.index(each.off)]
        assert row.kind is RowKind.EXIT
        assert row.parent is not None
        parent = reach.rows[row.parent]
        assert parent.place.stop_id == each.junction
        assert row.depth == parent.depth
    assert [row.kind for row in reach.rows].count(RowKind.EXIT) == len(exits)


def test_exit_row_name() -> None:
    """The off station's name loses ' Station', then come the lines."""
    place = StopPlace(
        stop_id='X', name='North Strathfield Station', point=None,
    )
    assert exit_name(place=place, lines=('T9', 'CCN')) == (
        'North Strathfield · T9 CCN'
    )
    assert exit_name(place=place, lines=()) == 'North Strathfield'


def test_one_turnoff_makes_one_exit() -> None:
    """At Strathfield, expresses to Rhodes and Epping hook at N Strathfield."""
    tails = [
        tail('NS CW RH EP', 20),
        tail('RH EP', 6),
        tail('EP', 4),
        tail('J', 30),
    ]
    line = frozenset({'SF', 'J'})
    leads = exit_leads(tails=tails, line=line, origin='SF')
    assert leads == {('SF', 'RH'): ('SF', 'NS'), ('SF', 'EP'): ('SF', 'NS')}
    exits = grouped_exits(
        exits=[
            LineExit(junction='SF', off='EP', lines=('CCN',), trips=4),
            LineExit(junction='SF', off='NS', lines=('T2', 'T9'), trips=20),
            LineExit(junction='SF', off='RH', lines=('T9', 'NRC'), trips=6),
        ],
        leads=leads,
    )
    assert exits == (
        LineExit(
            junction='SF', off='NS', lines=('T2', 'T9', 'NRC', 'CCN'),
            trips=30,
        ),
    )


def test_separate_turnoffs_stay_apart() -> None:
    """Ways off whose trains never meet keep their own exits."""
    tails = [tail('J W1 W2', 5), tail('J R1', 3)]
    assert not exit_leads(tails=tails, line=LINE, origin='C')


def test_an_express_back_on_the_line_joins_the_turnoff() -> None:
    """At Campbelltown, an express to Central hooks at Leumeah."""
    tails = [tail('LE MI GL CA CE', 20), tail('CE', 2), tail('MA', 30)]
    line = frozenset({'CT', 'MA', 'CA'})
    assert exit_leads(tails=tails, line=line, origin='CT') == {
        ('CT', 'CE'): ('CT', 'LE'),
    }


def test_a_loop_run_the_other_way_is_another_turnoff() -> None:
    """Round a loop each way, the first stops lead separate turnoffs."""
    tails = [tail('TH WY CQ SJ MU', 20), tail('MU SJ CQ WY TH', 20)]
    assert not exit_leads(tails=tails, line=frozenset({'CE'}), origin='CE')


def test_a_way_crossing_another_is_another_turnoff() -> None:
    """A way off whose first stop another passes, then leaves, stays."""
    tails = [tail('MU SJ CQ WY TH', 20), tail('TH MP KC', 10)]
    assert not exit_leads(tails=tails, line=frozenset({'CE'}), origin='CE')
