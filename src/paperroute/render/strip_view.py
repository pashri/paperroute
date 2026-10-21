"""Draw the Strip and Strip + map frames."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.stop_view import Pill, Row, arrival_line
from paperroute.core.strip_badges import HALF_WIDE
from paperroute.core.strip_model import (
    PANE_TRACK_X,
    TRACK_X,
    StripMapView,
    StripPanel,
    StripView,
)
from paperroute.render.canvas import HEIGHT, INK, PAPER, WIDTH, Canvas, Face
from paperroute.render.chrome import (
    ARROW_WIDTH,
    BANNER,
    HEADER,
    arrow,
    banner,
    countdown_slot,
    pill,
    route_pair,
    view_header,
)
from paperroute.render.map_view import draw_overlay, snap, wrap
from paperroute.render.stop_view import TAG_FACE, finish
from paperroute.render.strip import draw_band, draw_rows, draw_strip_markers

NAME_X: Final[int] = TRACK_X + int(HALF_WIDE)
TIME_RIGHT: Final[int] = 424
STRIP_RIGHT: Final[int] = 440
RULE_WIDTH: Final[int] = 3
BAND_LEFT: Final[int] = 8
BAND_RIGHT: Final[int] = STRIP_RIGHT - 4
PANE_NAME_X: Final[int] = PANE_TRACK_X + NAME_X - TRACK_X
PANE_TIME_RIGHT: Final[int] = 790
TEXT_LEFT: Final[int] = 460
TEXT_RIGHT: Final[int] = 784
TEXT_WIDTH: Final[int] = TEXT_RIGHT - TEXT_LEFT
RULE_LEFT: Final[int] = 456
RULE_RIGHT: Final[int] = 786
TITLE_FACE: Final[Face] = Face(weight=700, px=20)
TITLE_Y: Final[int] = 80
SUBTITLE_FACE: Final[Face] = Face(weight=400, px=15)
SUBTITLE_Y: Final[int] = 104
ROWS_TOP: Final[int] = 160
ROW_STEP: Final[int] = 70
LEAVES_FACE: Final[Face] = Face(weight=800, px=30)
ARRIVES_FACE: Final[Face] = Face(weight=400, px=16)
COUNTDOWN_FACE: Final[Face] = Face(weight=700, px=22)
HEADSIGN_FACE: Final[Face] = Face(weight=400, px=14)
TIME_DROP: Final[int] = 44  # baseline below the row's rule
SECOND_DROP: Final[int] = 62  # carry-on baseline below the row's rule
MIDDLE_X: Final[int] = 556  # arrive arrow, badge and carry-on line
BADGE_PX: Final[int] = 14
CARRY_GAP: Final[int] = 10  # pixels between the arrival and a carry-on
BADGE_DROP: Final[int] = 34  # badge centre below the row's rule
COUNTDOWN_ROOM: Final[int] = 84  # pixels kept for the countdown
CANCELLED_LEFT: Final[int] = 684
CANCELLED_TEXT: Final[str] = 'CANCELLED'
EMPTY_FACE: Final[Face] = Face(weight=700, px=15)
EMPTY_Y: Final[int] = 190
EMPTY_LEADING: Final[int] = 20
STRIP_EMPTY_FACE: Final[Face] = Face(weight=700, px=18)
STRIP_EMPTY_Y: Final[int] = 250
HINT_BOX: Final[tuple[int, int, int, int]] = (456, 420, 786, 464)
HINT_FACE: Final[Face] = Face(weight=700, px=14)
HINT_PAD: Final[int] = 6  # pixels each side of the hint text


def row_middle(canvas: Canvas, *, row: Row, top: int, whole: bool) -> int:
    """
    Draw a departure's route badge and headsign, and its arrival if any.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    top : int
        The row's rule.
    whole : bool
        True on the whole-stop board, which shows no arrival.

    Returns
    -------
    int
        Where a carry-on line starts.
    """
    x = MIDDLE_X + route_pair(
        canvas,
        x=MIDDLE_X,
        y=top + BADGE_DROP,
        route=row.route,
        onto=row.onto,
        px=BADGE_PX,
    ) + 8
    canvas.text(
        at=(x, top + 40),
        text=canvas.fit(
            text=row.destination,
            face=HEADSIGN_FACE,
            width=TEXT_RIGHT - COUNTDOWN_ROOM - x,
        ),
        face=HEADSIGN_FACE,
        grey=INK,
    )
    if whole:
        return MIDDLE_X
    arrival = canvas.text(
        at=(MIDDLE_X, top + SECOND_DROP), text=arrival_line(row=row),
        face=TAG_FACE, grey=INK,
    )
    return MIDDLE_X + round(arrival) + CARRY_GAP


def row_countdown(canvas: Canvas, *, row: Row, top: int) -> None:
    """
    Draw a departure's countdown, or a block when it is cancelled.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    top : int
        The row's rule.
    """
    if row.pill is Pill.CANCELLED:
        pill(
            canvas,
            box=(CANCELLED_LEFT, top + 20, RULE_RIGHT, top + 48),
            text=CANCELLED_TEXT,
            px=13,
        )
        return
    canvas.text(
        at=(TEXT_RIGHT, top + TIME_DROP),
        text=countdown_slot(row=row),
        face=COUNTDOWN_FACE,
        grey=INK,
        anchor='rs',
    )


def carry_on(canvas: Canvas, *, row: Row, top: int, left: int) -> None:
    """
    Draw where the vehicle runs on to, on a second line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure; nothing is drawn when it doesn't run on.
    top : int
        The row's rule.
    left : int
        Where the line starts.
    """
    then = row.then
    if then is None:
        return
    baseline = top + SECOND_DROP
    arrow(canvas, x=left, y=baseline - 4, grey=INK)
    x = left + ARROW_WIDTH + 4
    canvas.text(
        at=(x, baseline),
        text=canvas.fit(
            text=f'{then.route} {then.headsign}',
            face=TAG_FACE,
            width=TEXT_RIGHT - x,
        ),
        face=TAG_FACE,
        grey=INK,
    )


def panel_row(canvas: Canvas, *, row: Row, top: int, whole: bool) -> None:
    """
    Draw one departure in the panel under its rule.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    top : int
        The row's rule.
    whole : bool
        True on the whole-stop board.
    """
    canvas.rect(box=(RULE_LEFT, top - 2, RULE_RIGHT, top - 1), grey=INK)
    canvas.text(
        at=(TEXT_LEFT, top + TIME_DROP), text=row.leaves_at,
        face=LEAVES_FACE, grey=INK,
    )
    left = row_middle(canvas, row=row, top=top, whole=whole)
    row_countdown(canvas, row=row, top=top)
    carry_on(canvas, row=row, top=top, left=left)


def panel_heading(canvas: Canvas, *, panel: StripPanel, drop: int) -> None:
    """
    Draw the From and the Destination line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    panel : StripPanel
        The panel.
    drop : int
        Pixels to move it all down.
    """
    canvas.text(
        at=(TEXT_LEFT, TITLE_Y + drop),
        text=canvas.fit(text=panel.title, face=TITLE_FACE, width=TEXT_WIDTH),
        face=TITLE_FACE,
        grey=INK,
    )
    if panel.destination is not None:
        y = SUBTITLE_Y + drop
        x = TEXT_LEFT + arrow(canvas, x=TEXT_LEFT, y=y - 5, grey=INK)
        canvas.text(
            at=(x, y),
            text=canvas.fit(
                text=panel.destination,
                face=SUBTITLE_FACE,
                width=TEXT_RIGHT - x,
            ),
            face=SUBTITLE_FACE,
            grey=INK,
        )


def panel_empty(canvas: Canvas, *, text: str, drop: int) -> None:
    """
    Say why the panel lists no departures, over up to two lines.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The reason.
    drop : int
        Pixels to move it down.
    """
    lines = wrap(text=text, face=EMPTY_FACE, width=TEXT_WIDTH)
    for index, line in enumerate(lines[:2]):
        canvas.text(
            at=(TEXT_LEFT, EMPTY_Y + drop + index * EMPTY_LEADING),
            text=canvas.fit(text=line, face=EMPTY_FACE, width=TEXT_WIDTH),
            face=EMPTY_FACE,
            grey=INK,
        )


def hint_box(canvas: Canvas, *, text: str) -> None:
    """
    Draw the knob hint as paper text in an ink box at the panel's foot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The hint.
    """
    left, top, right, bottom = HINT_BOX
    canvas.rect(box=HINT_BOX, grey=INK)
    canvas.text(
        at=((left + right) / 2, (top + bottom) / 2),
        text=canvas.fit(
            text=text, face=HINT_FACE, width=right - left - 2 * HINT_PAD,
        ),
        face=HINT_FACE,
        grey=PAPER,
        anchor='mm',
    )


def draw_panel(canvas: Canvas, *, panel: StripPanel, drop: int) -> None:
    """
    Draw the departures panel right of the strip.

    Parameters
    ----------
    canvas : Canvas
        Target.
    panel : StripPanel
        The panel.
    drop : int
        Pixels to move all but the hint down, below a banner.
    """
    panel_heading(canvas, panel=panel, drop=drop)
    for index, row in enumerate(panel.rows):
        panel_row(
            canvas,
            row=row,
            top=ROWS_TOP + drop + index * ROW_STEP,
            whole=panel.no_destination,
        )
    if not panel.rows and panel.empty:
        panel_empty(canvas, text=panel.empty, drop=drop)
    if panel.hint is not None:
        hint_box(canvas, text=panel.hint)


def draw_strip(canvas: Canvas, *, view: StripView) -> None:
    """
    Draw the strip column: rows, vehicles and band, or why there are none.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : StripView
        The view.
    """
    with canvas.clipped(box=(0, HEADER, STRIP_RIGHT, HEIGHT)):
        draw_rows(
            canvas,
            rows=view.rows,
            track_x=TRACK_X,
            name_x=NAME_X,
            time_right=TIME_RIGHT,
            right=STRIP_RIGHT,
        )
        draw_strip_markers(canvas, markers=view.markers)
        if view.band is not None:
            draw_band(canvas, band=view.band, left=BAND_LEFT, right=BAND_RIGHT)
    if view.empty:
        canvas.text(
            at=(STRIP_RIGHT / 2, STRIP_EMPTY_Y),
            text=canvas.fit(
                text=view.empty, face=STRIP_EMPTY_FACE, width=STRIP_RIGHT - 64,
            ),
            face=STRIP_EMPTY_FACE,
            grey=INK,
            anchor='mm',
        )


def paint_strip_view(canvas: Canvas, *, view: StripView) -> None:
    """
    Draw a Strip view onto a canvas in ink and paper only.

    Parameters
    ----------
    canvas : Canvas
        Target, aliased.
    view : StripView
        View-model.
    """
    draw_strip(canvas, view=view)
    canvas.rect(
        box=(STRIP_RIGHT, HEADER, STRIP_RIGHT + RULE_WIDTH, HEIGHT), grey=INK,
    )
    draw_panel(canvas, panel=view.panel, drop=BANNER if view.banner else 0)
    view_header(canvas, view=view, active='Strip', picking=view.picking)
    if view.banner:
        banner(canvas, text=view.banner)


def draw_strip_view(*, view: StripView) -> NDArray[np.uint8]:
    """
    Draw a Strip view as a 1-bit frame.

    Parameters
    ----------
    view : StripView
        View-model.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    paint_strip_view(canvas, view=view)
    return finish(canvas)


def draw_pane(canvas: Canvas, *, view: StripMapView) -> None:
    """
    Draw the rule and the strip right of the map.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : StripMapView
        The view.
    """
    left = view.map.base.width
    canvas.rect(box=(left, HEADER, left + RULE_WIDTH, HEIGHT), grey=INK)
    with canvas.clipped(box=(left + RULE_WIDTH, HEADER, WIDTH, HEIGHT)):
        draw_rows(
            canvas,
            rows=view.pane.rows,
            track_x=PANE_TRACK_X,
            name_x=PANE_NAME_X,
            time_right=PANE_TIME_RIGHT,
            right=WIDTH,
        )
        draw_strip_markers(canvas, markers=view.pane.markers)


def draw_strip_map_view(
    *,
    view: StripMapView,
    base: NDArray[np.uint8],
) -> NDArray[np.uint8]:
    """
    Draw a Strip + map frame over a cached map base.

    Parameters
    ----------
    view : StripMapView
        View-model.
    base : NDArray[np.uint8]
        The map base, ``MAP_HEIGHT`` by ``view.map.base.width``; never
        changed.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels: four greys in the map, ink and paper elsewhere.
    """
    canvas = Canvas()
    canvas.image(grey=base, at=(0, HEADER))
    draw_overlay(canvas, view=view.map)
    draw_pane(canvas, view=view)
    view_header(canvas, view=view.map, active='Strip + map')
    if view.map.banner:
        banner(canvas, text=view.map.banner)
    return snap(grey=canvas.grey(), width=view.map.base.width)
