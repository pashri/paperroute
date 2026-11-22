"""Tests for where a strip's row slots sit."""

from paperroute_testkit.core.reach_factories import reach_of, tail
from paperroute_transit.core.reach import Reach, ReachInput, build_reach
from paperroute_transit.core.strip_model import (
    PANE_FROM_ROW,
    PANE_ROWS,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    Window,
)
from paperroute_transit.core.strip_slots import from_slot, home_of, window_of


def test_window_of_starts_at_the_given_top() -> None:
    """A window starts at its top, one slot lower under a banner."""
    assert window_of(banner=False) == Window(top=STRIP_TOP, rows=STRIP_ROWS)
    assert window_of(banner=False, rows=9, top=152) == Window(top=152, rows=9)
    assert window_of(banner=True, rows=9, top=152) == Window(
        top=152 + ROW_H,
        rows=8,
    )


def both_reach() -> Reach:
    """Six stations each way: A6 to A1 above C, B1 to B6 below."""
    here = tail('B1 B2 B3 B4 B5 B6', 6)
    there = tail('A1 A2 A3 A4 A5 A6', 6)
    places = {
        row.place.stop_id: row.place
        for row in reach_of(tails=[here, there]).rows
    }
    return build_reach(data=ReachInput(
        origin=places['C'], tails=(here,), places=places, station_of={},
        across_tails=(there,),
    ))


def test_from_slot_is_the_middle_for_both_sides() -> None:
    """The upper middle slot for both kerbs; the third slot otherwise."""
    assert from_slot(rows=STRIP_ROWS, middle=True) == 5
    assert from_slot(rows=PANE_ROWS, middle=True) == 4
    assert from_slot(rows=PANE_ROWS - 1, middle=True) == 3
    assert from_slot(rows=STRIP_ROWS, middle=False) == PANE_FROM_ROW


def test_home_of_puts_a_both_sides_from_in_the_middle() -> None:
    """The From, row 6, rests in slot 5 of 12 and slot 4 of 9."""
    reach = both_reach()
    assert home_of(
        reach=reach, destination=None, rows=STRIP_ROWS, middle=True,
    ) == 1
    assert home_of(
        reach=reach, destination=None, rows=PANE_ROWS, middle=True,
    ) == 2
    assert home_of(reach=reach, destination=None, rows=STRIP_ROWS) == 4
