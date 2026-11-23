"""Facts about drawn pair rows (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import (
    INK,
    PAPER,
    WIDTH,
    Canvas,
    text_width,
)
from paperroute_device.render.frame import finish
from paperroute_transit.core.connection_view import PairRow
from paperroute_transit.render.chrome import LINK_WIDTH, badge_width
from paperroute_transit.render.pair_list import (
    ARRIVE_FACE,
    ARRIVE_ROOM,
    CHIP_PX,
    DETAIL_FACE,
    INSET,
    LEAVE_FACE,
    MIN_UNIT,
    ROUTES_CLEAR,
    ROUTES_LEFT,
    STRUCK_FACE,
    STRUCK_GAP,
    draw_pair_row,
    route_chips,
    routes_left,
)
from tests.render.test_chrome import Recording

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
TIMED: Final[PairRow] = replace(LIVE, live=False)


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


def test_uncertain_pair_row_starts_its_detail_line_with_it() -> None:
    """'uncertain' begins the detail line, under the routes."""
    frames = []
    for row in (LIVE, replace(LIVE, uncertain=True)):
        canvas = Canvas()
        draw_pair_row(
            canvas, row=row, y=TOP, edges=(LEFT, RIGHT), detail=row.detail,
        )
        frames.append(finish(canvas))
    found = np.argwhere(frames[0] != frames[1])
    assert found.size
    assert found[:, 0].min() >= TOP + 38 and found[:, 0].max() <= TOP + 54
    assert found[:, 1].min() >= LEFT + ROUTES_LEFT


def test_routes_move_only_for_a_marked_leave_time() -> None:
    """Unmarked rows keep the column; a mark pushes it clear."""
    assert routes_left(rows=(LIVE, TIMED)) == ROUTES_LEFT
    late_night = replace(TIMED, leaves_at='23:58')
    assert routes_left(rows=(LIVE, late_night)) == ROUTES_LEFT
    marked = replace(TIMED, mark='+12')
    left = routes_left(rows=(LIVE, marked))
    assert left > ROUTES_LEFT
    canvas = Canvas()
    draw_pair_row(
        canvas, row=marked, y=TOP, edges=(LEFT, RIGHT),
        detail=marked.detail, routes=left,
    )
    clear = canvas.grey()[
        TOP + 2:TOP + 36, int(LEFT + left - ROUTES_CLEAR + 1):int(LEFT + left)
    ]
    assert (clear >= 128).all()


def test_tight_and_uncertain_row_reads_tight_first() -> None:
    """The bold 'tight' comes before 'uncertain · at ...'."""
    canvas = Recording()
    row = replace(LIVE, tight=True, uncertain=True)
    draw_pair_row(
        canvas, row=row, y=TOP, edges=(LEFT, RIGHT), detail=row.detail,
    )
    detail = next(
        index for index, text in enumerate(canvas.drawn)
        if text.startswith('uncertain · at Constitution')
    )
    assert canvas.drawn.index('tight') < detail


def test_missed_row_strikes_the_old_arrival() -> None:
    """The old arrival sits struck through, just left of the new one."""
    canvas = Recording()
    row = replace(LIVE, was='8:12', arrives_at='8:27')
    draw_pair_row(
        canvas, row=row, y=TOP, edges=(LEFT, RIGHT), detail=row.detail,
    )
    assert {'8:12', '8:27'} <= set(canvas.drawn)
    end = (
        RIGHT - INSET - text_width(text='8:27', face=ARRIVE_FACE) - STRUCK_GAP
    )
    start = end - text_width(text='8:12', face=STRUCK_FACE)
    assert (canvas.grey()[TOP + 27, int(start) + 1:int(end) - 1] < 128).all()


def test_stranded_row_strikes_its_arrival_and_shows_none() -> None:
    """With no new arrival the struck one ends at the right edge."""
    canvas = Recording()
    row = replace(LIVE, was='9:04', arrives_at='')
    draw_pair_row(
        canvas, row=row, y=TOP, edges=(LEFT, RIGHT), detail=row.detail,
    )
    assert '9:04' in canvas.drawn
    ink = np.flatnonzero((canvas.grey()[TOP + 18:TOP + 34] < 128).any(axis=0))
    assert RIGHT - INSET - 2 <= ink.max() <= RIGHT - INSET
