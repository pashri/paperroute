"""Tests for parent stations whose two kerbs face different ways."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import place
from paperroute_transit.core.parent_sides import kerb_sides, line_pairs
from paperroute_transit.core.reach import RunTail, StripKey
from paperroute_transit.core.sides import KerbPair, ParentKerb, ParentKerbs
from tests.core.chooser_walks import (
    KERB_LINE,
    PARENT_KERBS,
    PARENT_PAIR,
    QUIET_PAIR,
    S2A,
    S2B,
)

C_KERBS: Final[ParentKerbs] = ParentKerbs(
    station='C',
    kerbs=(
        ParentKerb(place=place('C1'), onward=frozenset({'J'}), trips=9),
        ParentKerb(place=place('C2'), onward=frozenset({'E1'}), trips=7),
    ),
    busy=False,
)
WEST: Final[RunTail] = RunTail(before=('E1',), after=('J', 'W1'), trips=6)
EAST: Final[RunTail] = RunTail(before=('J',), after=('E1', 'E2'), trips=5)
ENDS: Final[RunTail] = RunTail(before=('E1',), after=(), trips=1)
ELSEWHERE: Final[RunTail] = RunTail(before=(), after=('Q',), trips=1)


def test_kerb_sides_splits_a_parent_by_kerb() -> None:
    """Each kerb keeps the tails that leave toward its stations."""
    key = StripKey(station='C1', kind=None, routes=(), across='C2')
    assert kerb_sides(
        key=key, tails=(WEST, EAST), across_tails=(), parents=C_KERBS,
    ) == ((WEST,), (EAST,))


def test_kerb_sides_drops_a_run_ending_at_the_parent() -> None:
    """A run with nothing after the From, or going elsewhere, is dropped."""
    key = StripKey(station='C1', kind=None, routes=(), across='C2')
    assert kerb_sides(
        key=key, tails=(ENDS, ELSEWHERE, WEST, EAST), across_tails=(),
        parents=C_KERBS,
    ) == ((WEST,), (EAST,))


def test_kerb_sides_keeps_one_kerb_to_its_way() -> None:
    """A key with no kerb across keeps only its kerb's way."""
    key = StripKey(station='C2', kind=None, routes=())
    assert kerb_sides(
        key=key, tails=(WEST, EAST), across_tails=(), parents=C_KERBS,
    ) == ((EAST,), ())


def test_kerb_sides_leaves_a_station_pin_as_today() -> None:
    """A station key, or no parent, comes back unchanged."""
    key = StripKey(station='C', kind=None, routes=())
    assert kerb_sides(
        key=key, tails=(WEST, EAST), across_tails=(), parents=C_KERBS,
    ) == ((WEST, EAST), ())
    assert kerb_sides(
        key=replace(key, across='Q'), tails=(WEST,),
        across_tails=(ELSEWHERE,), parents=None,
    ) == ((WEST,), (ELSEWHERE,))


def test_line_pairs_orients_a_parent_by_the_next_station() -> None:
    """A parent's pair has the kerb whose buses call at the next station."""
    parents = {'s2': PARENT_KERBS}
    assert line_pairs(line=KERB_LINE, pairs={}, parents=parents) == (
        PARENT_PAIR,
    )
    assert line_pairs(line=KERB_LINE[::-1], pairs={}, parents=parents) == (
        KerbPair(station='s2', other=S2A, busy=False, here=S2B),
    )


def test_line_pairs_orients_the_last_station_by_the_one_before() -> None:
    """The line's last station takes the kerb not heading back."""
    assert line_pairs(
        line=KERB_LINE[:3], pairs={}, parents={'s2': PARENT_KERBS},
    ) == (PARENT_PAIR,)


def test_line_pairs_skips_a_parent_the_line_cannot_orient() -> None:
    """A parent with no usable neighbour on the line has no pair."""
    one, two = PARENT_KERBS.kerbs
    away = frozenset({'elsewhere'})
    lost = replace(PARENT_KERBS, kerbs=(
        replace(one, onward=away), replace(two, onward=away),
    ))
    assert line_pairs(line=KERB_LINE, pairs={}, parents={'s2': lost}) == ()
    assert line_pairs(
        line=KERB_LINE[2:3], pairs={}, parents={'s2': PARENT_KERBS},
    ) == ()


def test_line_pairs_prefers_the_parent_and_drops_a_pair_with_itself() -> None:
    """The parent's pair wins; a pair whose other kerb is the station goes."""
    assert line_pairs(
        line=KERB_LINE, pairs={'s2': QUIET_PAIR},
        parents={'s2': PARENT_KERBS},
    ) == (PARENT_PAIR,)
    assert line_pairs(
        line=KERB_LINE, pairs={'s2': QUIET_PAIR}, parents={},
    ) == (QUIET_PAIR,)
    itself = replace(QUIET_PAIR, other=place('s2', name='Orchard Close'))
    assert line_pairs(line=KERB_LINE, pairs={'s2': itself}, parents={}) == ()


def test_line_pairs_reads_a_station_once_at_its_first_call() -> None:
    """A station a loop calls at twice is paired once."""
    loop = (*KERB_LINE[:4], KERB_LINE[2])
    assert line_pairs(
        line=loop, pairs={}, parents={'s2': PARENT_KERBS},
    ) == (PARENT_PAIR,)
