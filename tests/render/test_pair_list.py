"""Facts about drawn pair rows (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.render.chrome import LINK_WIDTH, badge_width
from paperroute.render.pair_list import (
    ARRIVE_ROOM,
    CHIP_PX,
    DETAIL_FACE,
    INSET,
    LEAVE_FACE,
    MIN_UNIT,
    ROUTES_LEFT,
    draw_pair_row,
    route_chips,
)
from paperroute_device.render.canvas import (
    INK,
    PAPER,
    WIDTH,
    Canvas,
    text_width,
)
from paperroute_device.render.frame import finish
from paperroute_transit.core.connection_view import PairRow

LEFT: Final[int] = 450
RIGHT: Final[int] = 790
TOP: Final[int] = 100
SIDEBAR_LEFT: Final[int] = 523
SIDEBAR_ROOM: Final[int] = 143
LIVE: Final[PairRow] = PairRow(
    leaves_at='8:09',
    chips=('606', '664'),
    detail='at Constitution Hill, North West Twy',
    arrives_at='8:33',
    minutes=24,
    countdown='12',
    live=True,
)


def unit_band(*, row: PairRow) -> NDArray[np.uint8]:
    """
    Draw a row and cut out the band under its leave time.

    Parameters
    ----------
    row : PairRow
        The row.

    Returns
    -------
    NDArray[np.uint8]
        The pixels where `min` goes under a count.
    """
    canvas = Canvas()
    draw_pair_row(
        canvas, row=row, y=TOP, edges=(LEFT, RIGHT), detail=row.detail,
    )
    frame = finish(canvas)
    return frame[
        TOP + 38 : TOP + 54,
        LEFT + INSET : LEFT + ROUTES_LEFT - 4,
    ]


def test_live_pair_row_draws_count_with_min_under() -> None:
    """A live count has `min` under it; a clock time has nothing."""
    assert unit_band(row=LIVE).min() == 0
    assert unit_band(row=replace(LIVE, live=False)).min() == PAPER


def test_pair_row_now_has_no_unit() -> None:
    """A live 'now' draws alone."""
    assert unit_band(row=replace(LIVE, countdown='now')).min() == PAPER


def test_widest_pair_count_fits_before_the_routes() -> None:
    """'100' and `min` both end before the route chips."""
    count = text_width(text='100', face=LEAVE_FACE)
    unit = text_width(text=MIN_UNIT, face=DETAIL_FACE)
    assert INSET + count + 4 <= ROUTES_LEFT
    assert INSET + unit + 4 <= ROUTES_LEFT


@pytest.mark.parametrize(
    'chips', [('T1', '711', 'M1'), ('SH100', '711', 'BMT')],
)
def test_three_chips_keep_full_labels_in_the_sidebar(
    chips: tuple[str, ...],
) -> None:
    """Three chips fit the Map sidebar's list with full-width badges."""
    width = route_chips(
        Canvas(), x=0, y=20, chips=chips, room=SIDEBAR_ROOM, px=CHIP_PX,
    )
    assert width == pytest.approx(3 * badge_width(px=10) + 2 * LINK_WIDTH)
    assert width <= SIDEBAR_ROOM


def test_three_label_sidebar_row_clears_the_arrival() -> None:
    """Three chips end clear of the arrival time, in real ink."""
    canvas = Canvas()
    y = 100
    row = replace(
        LIVE, chips=('SH100', '711', 'BMT'), arrives_at='23:58',
    )
    draw_pair_row(
        canvas, row=row, y=y, edges=(SIDEBAR_LEFT, WIDTH), detail='',
    )
    frame = finish(canvas)
    ink = np.flatnonzero((frame[y + 10 : y + 31] == INK).any(axis=0))
    routes = SIDEBAR_LEFT + ROUTES_LEFT
    chips = ink[(ink >= routes) & (ink <= WIDTH - ARRIVE_ROOM)]
    chips_right = chips.max()
    arrive_left = ink[ink > chips_right + 1].min()
    assert chips_right <= routes + SIDEBAR_ROOM
    assert arrive_left - chips_right >= 8
