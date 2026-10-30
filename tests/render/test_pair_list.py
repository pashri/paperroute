"""Facts about drawn pair rows (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.connection_view import PairRow
from paperroute.render.canvas import PAPER, Canvas, text_width
from paperroute.render.pair_list import (
    DETAIL_FACE,
    INSET,
    LEAVE_FACE,
    MIN_UNIT,
    ROUTES_LEFT,
    draw_pair_row,
)
from paperroute.render.stop_view import finish

LEFT: Final[int] = 450
RIGHT: Final[int] = 790
TOP: Final[int] = 100
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
