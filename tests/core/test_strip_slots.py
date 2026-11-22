"""Tests for where a strip's row slots sit."""

from paperroute_testkit.core.reach_factories import both_reach
from paperroute_transit.core.reach import StripKey
from paperroute_transit.core.strip_model import (
    PANE_FROM_ROW,
    PANE_ROWS,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    Window,
)
from paperroute_transit.core.strip_slots import (
    both_kerbs,
    from_slot,
    home_of,
    window_of,
)


def test_window_of_starts_at_the_given_top() -> None:
    """A window starts at its top, one slot lower under a banner."""
    assert window_of(banner=False) == Window(top=STRIP_TOP, rows=STRIP_ROWS)
    assert window_of(banner=False, rows=9, top=152) == Window(top=152, rows=9)
    assert window_of(banner=True, rows=9, top=152) == Window(
        top=152 + ROW_H,
        rows=8,
    )


def test_from_slot_is_the_middle_for_both_sides() -> None:
    """The upper middle slot for both kerbs; the third slot otherwise."""
    assert from_slot(rows=STRIP_ROWS, middle=True) == 5
    assert from_slot(rows=PANE_ROWS, middle=True) == 4
    assert from_slot(rows=PANE_ROWS - 1, middle=True) == 3
    assert from_slot(rows=STRIP_ROWS, middle=False) == PANE_FROM_ROW


def test_home_of_puts_a_both_sides_from_in_the_middle() -> None:
    """The From, row 6, rests in slot 5 of 12 and slot 4 of 9."""
    reach = both_reach(stations=6)
    assert home_of(
        reach=reach, destination=None, rows=STRIP_ROWS, middle=True,
    ) == 1
    assert home_of(
        reach=reach, destination=None, rows=PANE_ROWS, middle=True,
    ) == 2
    assert home_of(reach=reach, destination=None, rows=STRIP_ROWS) == 4


def test_both_kerbs_is_true_with_a_kerb_across() -> None:
    """Only a key naming a kerb across is fed by both kerbs."""
    key = StripKey(station='C', kind='bus', routes=())
    assert both_kerbs(key=StripKey(
        station='C', kind='bus', routes=(), across='x2',
    ))
    assert not both_kerbs(key=key)
    assert not both_kerbs(key=None)
