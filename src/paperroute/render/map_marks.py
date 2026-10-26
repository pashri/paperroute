"""The map's fixed marks: route line widths and the From stop."""

from typing import Final

from paperroute.core.geo import Point
from paperroute.render.canvas import INK, PAPER, Canvas

ROUTE_STYLE: Final[dict[int, tuple[int, int | None]]] = {
    3: (7, 3),
    5: (7, 3),
    8: (7, 3),
    14: (5, 1),
    45: (3, None),
}
STOP_DISC: Final[float] = 12.0
STOP_RING: Final[float] = 9.0
STOP_RING_WIDTH: Final[float] = 3.0
STOP_DOT: Final[float] = 4.0


def draw_stop(canvas: Canvas, *, at: Point) -> None:
    """
    Draw the From stop marker: a paper disc, an ink ring and an ink dot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : Point
        The stop's centre on the canvas.
    """
    x, y = at
    canvas.ellipse(
        box=(x - STOP_DISC, y - STOP_DISC, x + STOP_DISC, y + STOP_DISC),
        grey=PAPER,
    )
    canvas.ellipse(
        box=(x - STOP_RING, y - STOP_RING, x + STOP_RING, y + STOP_RING),
        grey=INK,
        stroke=STOP_RING_WIDTH,
    )
    canvas.ellipse(
        box=(x - STOP_DOT, y - STOP_DOT, x + STOP_DOT, y + STOP_DOT),
        grey=INK,
    )
