"""Tests for parent stations whose two kerbs face different ways."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import place
from paperroute_transit.core.parent_sides import kerb_sides
from paperroute_transit.core.reach import RunTail, StripKey
from paperroute_transit.core.sides import ParentKerb, ParentKerbs

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
