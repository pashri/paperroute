"""Draw the Map view: header, map with markers, sidebar and banner."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapView,
    screen_point,
)
from paperroute.core.stop_view import Pill, Row
from paperroute.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    Box,
    Canvas,
    Face,
    text_width,
)
from paperroute.render.chrome import (
    BANNER,
    FASTER,
    HEADER,
    badge,
    banner,
    cancelled,
    leaves,
    pill,
    view_header,
)
from paperroute.render.map_base import HALO, NOT_INSTALLED_DROP, draw_stop
from paperroute.render.markers import draw_markers
from paperroute.render.tile_codec import THRESHOLD, quantise4

SIDEBAR_LEFT: Final[int] = 520
RULE_WIDTH: Final[int] = 3
SIDEBAR_TEXT: Final[int] = 538
SIDEBAR_RIGHT: Final[int] = 784
MAP_BOX: Final[Box] = (0, HEADER, SIDEBAR_LEFT, HEIGHT)
EMPTY_FACE: Final[Face] = Face(weight=700, px=22)
EMPTY_FACES: Final[tuple[Face, ...]] = (
    EMPTY_FACE,
    Face(weight=700, px=18),
    Face(weight=700, px=15),
)
EMPTY_LINES: Final[int] = 2
EMPTY_LEADING: Final[int] = 4  # pixels between the empty text's lines
EMPTY_MARGIN: Final[int] = 32  # pixels each side of the empty text
EMPTY_DROP: Final[float] = NOT_INSTALLED_DROP + 26.0  # below the centre
DESTINATION_FACE: Final[Face] = Face(weight=700, px=16)
DESTINATION_Y: Final[int] = 70
DESTINATION_WIDTH: Final[int] = 250
ROWS_TOP: Final[int] = 110
ROW_STEP: Final[int] = 72
BADGE_PX: Final[int] = 17
COUNTDOWN_FACE: Final[Face] = Face(weight=800, px=34)
COUNTDOWN_DROP: Final[int] = 12  # baseline below the badge centre
ARRIVES_FACE: Final[Face] = Face(weight=400, px=14)
ARRIVES_DROP: Final[int] = 26  # baseline below the badge centre
ROW_RULE_DROP: Final[int] = 40  # below the badge centre
ROW_RULE_LEFT: Final[int] = 534
ROW_RULE_RIGHT: Final[int] = 788
CANCELLED_LEFT: Final[int] = 630
CANCELLED_HALF: Final[int] = 13  # pixels above and below the badge centre
CANCELLED_PX: Final[int] = 15
FASTER_LEFT: Final[int] = 710
FASTER_TOP: Final[int] = 17  # below the badge centre
FASTER_BOTTOM: Final[int] = 32  # below the badge centre
FASTER_PX: Final[int] = 11


def wrap(*, text: str, face: Face, width: float) -> tuple[str, ...]:
    """
    Break text into lines at spaces, filling each line greedily.

    Parameters
    ----------
    text : str
        Text.
    face : Face
        Font.
    width : float
        Available width in pixels.

    Returns
    -------
    tuple[str, ...]
        Lines; a single word wider than the width gets a line of its own.
    """
    lines: list[str] = []
    for word in text.split():
        joined = f'{lines[-1]} {word}' if lines else word
        if lines and text_width(text=joined, face=face) <= width:
            lines[-1] = joined
        else:
            lines.append(word)
    return tuple(lines)


def empty_lines(*, text: str) -> tuple[tuple[str, ...], Face]:
    """
    Fit the empty text into at most two lines across the map.

    Parameters
    ----------
    text : str
        Empty text.

    Returns
    -------
    tuple[tuple[str, ...], Face]
        The lines and the face, the largest of `EMPTY_FACES` that fits;
        the smallest face's lines when none does.
    """
    width = MAP_WIDTH - 2 * EMPTY_MARGIN
    for face in EMPTY_FACES:
        lines = wrap(text=text, face=face, width=width)
        fits = all(text_width(text=line, face=face) <= width for line in lines)
        if len(lines) <= EMPTY_LINES and fits:
            return lines, face
    return lines, face


def draw_empty(canvas: Canvas, *, text: str) -> None:
    """
    Draw the empty text below the stop, one line under another.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        Empty text.
    """
    lines, face = empty_lines(text=text)
    top = HEADER + MAP_HEIGHT / 2 + EMPTY_DROP
    for index, line in enumerate(lines):
        canvas.text(
            at=(MAP_WIDTH / 2, top + index * (face.px + EMPTY_LEADING)),
            text=line,
            face=face,
            grey=INK,
            anchor='mm',
            halo=HALO,
        )


def draw_overlay(canvas: Canvas, *, view: MapView) -> None:
    """
    Draw the markers, the empty text and the From stop over the map.

    The empty text sits below the stop, under the "Map not installed"
    note, and over the markers. The stop is drawn last so nothing buries
    it.

    Parameters
    ----------
    canvas : Canvas
        Target, with the base already drawn below the header.
    view : MapView
        View-model.
    """
    centre = view.base.centre
    stop = (
        None
        if centre is None
        else screen_point(centre=centre, mpp=view.base.mpp, point=centre)
    )
    with canvas.clipped(box=MAP_BOX):
        draw_markers(
            canvas,
            markers=view.markers,
            mpp=view.base.mpp,
            origin=(0, HEADER),
            stop=stop,
        )
        if view.empty:
            draw_empty(canvas, text=view.empty)
        if stop is not None:
            draw_stop(canvas, at=(stop[0], stop[1] + HEADER))


def row_time(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw when a sidebar departure leaves, or a block if it is cancelled.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        The badge's vertical centre.
    """
    if row.pill is Pill.CANCELLED:
        pill(
            canvas,
            box=(
                CANCELLED_LEFT,
                y - CANCELLED_HALF,
                SIDEBAR_RIGHT,
                y + CANCELLED_HALF,
            ),
            text=cancelled(row=row),
            px=CANCELLED_PX,
        )
        return
    canvas.text(
        at=(SIDEBAR_RIGHT, y + COUNTDOWN_DROP),
        text=leaves(row=row),
        face=COUNTDOWN_FACE,
        grey=INK,
        anchor='rs',
    )


def sidebar_row(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw one sidebar departure.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        The badge's vertical centre.
    """
    badge(canvas, x=SIDEBAR_TEXT, y=y, route=row.route, px=BADGE_PX)
    row_time(canvas, row=row, y=y)
    canvas.text(
        at=(SIDEBAR_TEXT, y + ARRIVES_DROP),
        text=f'arr {row.arrives_at}',
        face=ARRIVES_FACE,
        grey=INK,
    )
    if row.pill is Pill.FASTER:
        pill(
            canvas,
            box=(
                FASTER_LEFT,
                y + FASTER_TOP,
                SIDEBAR_RIGHT,
                y + FASTER_BOTTOM,
            ),
            text=FASTER,
            px=FASTER_PX,
        )
    canvas.rect(
        box=(
            ROW_RULE_LEFT,
            y + ROW_RULE_DROP,
            ROW_RULE_RIGHT,
            y + ROW_RULE_DROP + 1,
        ),
        grey=INK,
    )


def draw_sidebar(canvas: Canvas, *, view: MapView) -> None:
    """
    Draw the rule, the destination and the next departures.

    With a banner showing, everything but the rule moves down below it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : MapView
        View-model.
    """
    canvas.rect(
        box=(SIDEBAR_LEFT, HEADER, SIDEBAR_LEFT + RULE_WIDTH, HEIGHT),
        grey=INK,
    )
    drop = BANNER if view.banner else 0
    if view.destination is not None:
        canvas.text(
            at=(SIDEBAR_TEXT, DESTINATION_Y + drop),
            text=canvas.fit(
                text=f'to {view.destination}',
                face=DESTINATION_FACE,
                width=DESTINATION_WIDTH,
            ),
            face=DESTINATION_FACE,
            grey=INK,
        )
    for index, row in enumerate(view.sidebar):
        sidebar_row(canvas, row=row, y=ROWS_TOP + drop + index * ROW_STEP)


def snap(*, grey: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """
    Keep four greys in the map and only ink and paper elsewhere.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        The drawn frame.

    Returns
    -------
    NDArray[np.uint8]
        The finished frame.
    """
    out = np.where(grey < THRESHOLD, INK, PAPER).astype(np.uint8)
    out[HEADER:, :SIDEBAR_LEFT] = quantise4(grey=grey[HEADER:, :SIDEBAR_LEFT])
    return out


def draw_map_view(
    *,
    view: MapView,
    base: NDArray[np.uint8],
) -> NDArray[np.uint8]:
    """
    Draw a Map view frame over a cached map base.

    Parameters
    ----------
    view : MapView
        View-model.
    base : NDArray[np.uint8]
        The map base, ``MAP_HEIGHT`` by ``MAP_WIDTH``; never changed.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels: four greys in the map, ink and paper elsewhere.
    """
    canvas = Canvas()
    canvas.image(grey=base, at=(0, HEADER))
    draw_overlay(canvas, view=view)
    view_header(canvas, view=view, active='Map')
    draw_sidebar(canvas, view=view)
    if view.banner:
        banner(canvas, text=view.banner)
    return snap(grey=canvas.grey())
