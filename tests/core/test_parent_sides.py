"""Tests for parent stations whose two kerbs face different ways."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import place
from paperroute_transit.core.model import Pin, Way
from paperroute_transit.core.parent_sides import (
    busier,
    kerb_sides,
    line_pairs,
    parent_pin,
    parent_pins,
)
from paperroute_transit.core.reach import RunTail, StripKey
from paperroute_transit.core.sides import (
    KerbPair,
    ParentKerb,
    ParentKerbs,
    both_sides,
)
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
    assert not line_pairs(line=KERB_LINE, pairs={}, parents={'s2': lost})
    assert not line_pairs(
        line=KERB_LINE[2:3], pairs={}, parents={'s2': PARENT_KERBS},
    )


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
    assert not line_pairs(line=KERB_LINE, pairs={'s2': itself}, parents={})


def test_line_pairs_reads_a_station_once_at_its_first_call() -> None:
    """A station a loop calls at twice is paired once."""
    loop = (*KERB_LINE[:4], KERB_LINE[2])
    assert line_pairs(
        line=loop, pairs={}, parents={'s2': PARENT_KERBS},
    ) == (PARENT_PAIR,)


def _unorientable(*, both: bool) -> ParentKerbs:
    """Make `s2` whose kerbs hold neither or both of its neighbours."""
    one, two = PARENT_KERBS.kerbs
    onward = frozenset({'s1', 's3'}) if both else frozenset({'elsewhere'})
    return replace(PARENT_KERBS, kerbs=(
        replace(one, onward=onward), replace(two, onward=onward),
    ))


def test_line_pairs_falls_back_to_the_facing_pair_when_unoriented() -> None:
    """A parent no kerb of which holds the neighbour offers its facing pair."""
    lost = {'s2': _unorientable(both=False)}
    assert line_pairs(
        line=KERB_LINE, pairs={'s2': QUIET_PAIR}, parents=lost,
    ) == (QUIET_PAIR,)
    assert not line_pairs(line=KERB_LINE, pairs={}, parents=lost)


def test_line_pairs_falls_back_when_both_kerbs_hold_the_neighbour() -> None:
    """A parent with the neighbour on both kerbs is unoriented too."""
    both = {'s2': _unorientable(both=True)}
    assert line_pairs(
        line=KERB_LINE, pairs={'s2': QUIET_PAIR}, parents=both,
    ) == (QUIET_PAIR,)
    assert not line_pairs(line=KERB_LINE, pairs={}, parents=both)


def test_line_pairs_orients_the_first_station_by_the_next() -> None:
    """The line's first station takes the kerb heading to the next one."""
    assert line_pairs(
        line=KERB_LINE[2:], pairs={}, parents={'s2': PARENT_KERBS},
    ) == (PARENT_PAIR,)


ON_PARENT: Final[Pin] = Pin(
    origin=place('s2', name='Orchard Close'), kind='bus',
)


def test_parent_pin_takes_the_busier_kerb() -> None:
    """The busier kerb becomes the From and the other the opposite."""
    pin = parent_pin(pin=ON_PARENT, parents={'s2': PARENT_KERBS})
    assert (pin.origin, pin.opposite, pin.towards) == (S2A, S2B, ('', ''))
    assert both_sides(pin=pin) and pin.kind == 'bus'


def test_parent_pin_breaks_a_tie_by_stop_id() -> None:
    """Kerbs with as many trips are ordered by stop ID."""
    even = replace(PARENT_KERBS, kerbs=(
        replace(PARENT_KERBS.kerbs[0], trips=30), PARENT_KERBS.kerbs[1],
    ))
    assert busier(parents=even)[0].place == S2A
    flipped = replace(even, kerbs=(even.kerbs[1], even.kerbs[0]))
    assert busier(parents=flipped)[0].place == S2A


def test_parent_pin_leaves_other_pins_alone() -> None:
    """A pin that is not a plain From on the parent is not changed."""
    for pin in (
        replace(ON_PARENT, destination=place('s4')),
        replace(ON_PARENT, ways=(Way(changes=(), ticked=True),)),
        replace(ON_PARENT, opposite=place('x2')),
        replace(ON_PARENT, missing=True),
        Pin(origin=S2A),
    ):
        assert parent_pin(pin=pin, parents={'s2': PARENT_KERBS}) == pin


def test_parent_pin_leaves_a_parent_that_does_not_qualify() -> None:
    """A pin on a station with no kerbs read, or no table, is unchanged."""
    other = replace(ON_PARENT, origin=place('s9'))
    assert parent_pin(pin=other, parents={'s2': PARENT_KERBS}) == other
    assert parent_pin(pin=ON_PARENT, parents={}) == ON_PARENT
    assert parent_pins(pins=(other, ON_PARENT), parents={}) == (
        other, ON_PARENT,
    )


def test_parent_pins_keeps_a_pin_whose_kerbs_are_taken() -> None:
    """A pin that would equal an existing pin stays as it was."""
    taken = Pin(origin=S2A, opposite=S2B, kind='bus')
    kept = parent_pins(
        pins=(ON_PARENT, taken), parents={'s2': PARENT_KERBS},
    )
    assert kept == (ON_PARENT, taken)
    assert not any(pin.missing for pin in kept)


def test_parent_pins_twice_changes_nothing_more() -> None:
    """Converting converted pins gives the same pins."""
    parents = {'s2': PARENT_KERBS}
    once = parent_pins(pins=(ON_PARENT,), parents=parents)
    assert once != (ON_PARENT,)
    assert parent_pins(pins=once, parents=parents) == once


def test_parent_pins_keeps_nickname_and_place_in_the_list() -> None:
    """A converted pin keeps its nickname, filters and its position."""
    named = replace(
        ON_PARENT,
        nickname='Home',
        routes=('333',),
        only=('333',),
        never=('x',),
    )
    before, after = Pin(origin=place('a')), Pin(origin=place('b'))
    pins = parent_pins(
        pins=(before, named, after), parents={'s2': PARENT_KERBS},
    )
    assert pins[0] is before and pins[2] is after
    assert pins[1] == replace(
        named, origin=S2A, opposite=S2B, towards=('', ''),
    )
