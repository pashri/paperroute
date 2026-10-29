"""The map's corner boxes: the scale at the bottom left, the credit right."""

from typing import Final

from paperroute.build.map_style import DARK
from paperroute.core.map_view import MAP_HEIGHT
from paperroute.render.canvas import INK, PAPER, Box, Canvas, Face, text_width

SCALE_STEPS: Final[tuple[int, ...]] = (5000, 2000, 1000, 500, 200, 100)
SCALE_REACH: Final[float] = 143.0  # pixels
SCALE_TEXT_GAP: Final[float] = 6.0  # pixels from the bar's end
CREDIT: Final[str] = '© OpenStreetMap'
BOX_INSET: Final[float] = 8.0  # pixels from the map's edges to a box
BOX_PAD: Final[float] = 4.0  # paper round a box's contents
BOX_FACE: Final[Face] = Face(weight=700, px=12)
BOX_BAR: Final[float] = 4.0  # the scale bar's thickness
TICK_WIDTH: Final[float] = 2.0
TICK_HEIGHT: Final[float] = 10.0


def scale_metres(*, mpp: int) -> int:
    """
    Pick the scale bar's length.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    int
        The largest step that fits in 143 px, else the smallest step.
    """
    return next(
        (metres for metres in SCALE_STEPS if metres / mpp <= SCALE_REACH),
        SCALE_STEPS[-1],
    )


def scale_text(*, mpp: int) -> str:
    """
    Label the scale bar with its length.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    str
        For example ``200 m`` or ``2 km``.
    """
    metres = scale_metres(mpp=mpp)
    return f'{metres // 1000} km' if metres >= 1000 else f'{metres} m'


def box_rows() -> tuple[float, float]:
    """
    Give the top and bottom of both corner boxes.

    Returns
    -------
    tuple[float, float]
        Map rows; the bottom is `BOX_INSET` above the map's bottom and
        the box holds one line of `BOX_FACE` with `BOX_PAD` round it.
    """
    bottom = MAP_HEIGHT - BOX_INSET
    return bottom - BOX_FACE.px - 2 * BOX_PAD, bottom


def scale_box(*, mpp: int) -> Box:
    """
    Give the scale box's paper, in map pixels.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    Box
        Left, top, right, bottom; the dark edge lies 1 px outside.
    """
    top, bottom = box_rows()
    text_left = BOX_INSET + BOX_PAD + scale_metres(mpp=mpp) / mpp + (
        SCALE_TEXT_GAP
    )
    right = text_left + text_width(text=scale_text(mpp=mpp), face=BOX_FACE)
    return BOX_INSET, top, right + BOX_PAD, bottom


def credit_box(*, width: int) -> Box:
    """
    Give the credit box's paper, in map pixels.

    Parameters
    ----------
    width : int
        Map width in pixels.

    Returns
    -------
    Box
        Left, top, right, bottom; the dark edge lies 1 px outside.
    """
    top, bottom = box_rows()
    right = width - BOX_INSET
    left = right - text_width(text=CREDIT, face=BOX_FACE) - 2 * BOX_PAD
    return left, top, right, bottom


def draw_box(canvas: Canvas, *, box: Box) -> None:
    """
    Draw a paper box with a 1 px dark edge outside it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    box : Box
        Left, top, right, bottom.
    """
    left, top, right, bottom = box
    canvas.rect(box=(left - 1, top - 1, right + 1, bottom + 1), grey=DARK)
    canvas.rect(box=box, grey=PAPER)


def draw_boxed_scale(canvas: Canvas, *, mpp: int) -> None:
    """
    Draw the scale bar, its end ticks and its length in a box.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    mpp : int
        Metres per pixel.
    """
    box = scale_box(mpp=mpp)
    draw_box(canvas, box=box)
    left, base = box[0] + BOX_PAD, box[3] - BOX_PAD
    right = left + scale_metres(mpp=mpp) / mpp
    canvas.rect(box=(left, base - BOX_BAR, right, base), grey=INK)
    for x in (left, right - TICK_WIDTH):
        canvas.rect(
            box=(x, base - TICK_HEIGHT, x + TICK_WIDTH, base), grey=INK,
        )
    canvas.text(
        at=(right + SCALE_TEXT_GAP, base - 1),
        text=scale_text(mpp=mpp),
        face=BOX_FACE,
        grey=INK,
    )


def draw_boxed_credit(canvas: Canvas, *, width: int) -> None:
    """
    Draw the OpenStreetMap credit in a box at the bottom right.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    width : int
        Map width in pixels.
    """
    box = credit_box(width=width)
    draw_box(canvas, box=box)
    canvas.text(
        at=(box[2] - BOX_PAD, box[3] - BOX_PAD - 1),
        text=CREDIT,
        face=BOX_FACE,
        grey=INK,
        anchor='rs',
    )
