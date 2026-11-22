"""Tests for where a strip's row slots sit."""

from paperroute_transit.core.strip_model import (
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    Window,
)
from paperroute_transit.core.strip_slots import window_of


def test_window_of_starts_at_the_given_top() -> None:
    """A window starts at its top, one slot lower under a banner."""
    assert window_of(banner=False) == Window(top=STRIP_TOP, rows=STRIP_ROWS)
    assert window_of(banner=False, rows=9, top=152) == Window(top=152, rows=9)
    assert window_of(banner=True, rows=9, top=152) == Window(
        top=152 + ROW_H,
        rows=8,
    )
