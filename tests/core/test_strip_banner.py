"""Tests for the Strip and Strip + map pane under a live data banner."""

from dataclasses import replace
from typing import Final

from paperroute.core.strip_model import (
    PANE_ROWS,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    StripInputs,
)
from paperroute.core.strip_state import scroll_range
from paperroute.core.strip_view import build_strip_pane, build_strip_view
from tests.core.test_strip_view import inputs_of, long_reach

BANNER: Final[str] = 'No live data since 7:56'
UNDER: Final[float] = STRIP_TOP + ROW_H  # top slot's top under a banner


def bannered(*, scroll: int = 0, destination: str | None = None
             ) -> StripInputs:
    """
    Make long-reach inputs whose Stop view shows a banner.

    Parameters
    ----------
    scroll : int
        Rows scrolled down.
    destination : str | None
        Destination station.

    Returns
    -------
    StripInputs
        The inputs.
    """
    base = inputs_of(reach=long_reach(), destination=destination)
    return replace(
        base,
        scroll=scroll,
        stop_view=replace(base.stop_view, banner=BANNER),
    )


def test_strip_starts_below_the_banner() -> None:
    """A banner takes the top slot: eleven rows, FROM still third."""
    view = build_strip_view(inputs=bannered())
    assert len(view.rows) == STRIP_ROWS - 1
    assert view.rows[0].y == UNDER + ROW_H / 2
    assert view.rows[2].name == 'S20'
    assert view.rows[-1].y == STRIP_TOP + (STRIP_ROWS - 0.5) * ROW_H


def test_band_sits_below_the_banner() -> None:
    """The top band takes the first slot under the banner."""
    view = build_strip_view(inputs=bannered(scroll=20))
    assert view.band is not None
    assert view.band.top
    assert view.band.y == UNDER
    assert view.band.text == 'FROM S20 · 18 turns away'
    assert len(view.rows) == STRIP_ROWS - 2
    assert view.rows[0].y == UNDER + ROW_H * 1.5


def test_bottom_band_counts_the_shorter_strip() -> None:
    """With eleven slots the From is one more turn away below."""
    view = build_strip_view(inputs=bannered(scroll=-20))
    assert view.band is not None
    assert not view.band.top
    assert view.band.y == UNDER + (STRIP_ROWS - 2) * ROW_H
    assert view.band.text == 'FROM S20 · 12 turns away'


def test_long_ride_rests_with_from_in_the_last_slot() -> None:
    """A ride longer than the strip ends at the last slot under a banner."""
    view = build_strip_view(inputs=bannered(destination='S5'))
    assert [view.rows[0].name, view.rows[-1].name] == ['S10', 'S20']
    assert view.band is None


def test_scroll_range_uses_the_shorter_strip() -> None:
    """The scroll range follows the eleven-row home window."""
    reach = long_reach()
    assert scroll_range(
        reach=reach, destination='S5', banner=True,
    ) == (-10, 19)
    assert scroll_range(
        reach=reach, destination='S5', banner=False,
    ) == (-9, 19)
    assert scroll_range(
        reach=reach, destination=None, banner=True,
    ) == (-18, 40 - 11 - 18)


def test_pane_starts_below_the_banner() -> None:
    """Strip + map's pane drops a row under a banner, FROM still third."""
    pane = build_strip_pane(inputs=bannered())
    assert len(pane.rows) == PANE_ROWS - 1
    assert pane.rows[0].y == UNDER + ROW_H / 2
    assert pane.rows[2].name == 'S20'
