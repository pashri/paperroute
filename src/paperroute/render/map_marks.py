"""The map's fixed marks: route line widths, ridden stops and the From."""

from typing import Final

from paperroute.core.map_view import MapBase
from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_transit.core.geo import Point
from paperroute_transit.core.tiles import to_pixels

Window = tuple[int, int]

ROUTE_STYLE: Final[dict[int, tuple[int, int | None]]] = {
    3: (11, 5),
    5: (11, 5),
    8: (10, 4),
    14: (8, 2),
    25: (7, None),
    45: (6, None),
}
STOP_DISC: Final[float] = 16.0
STOP_RING: Final[float] = 12.0
STOP_RING_WIDTH: Final[float] = 4.0
STOP_DOT: Final[float] = 5.0
RIDDEN_RING: Final[float] = 5.0  # the smallest ring
RIDDEN_HOLE: Final[float] = 3.0  # the smallest ring's paper centre
RIDDEN_REACH: Final[float] = 2.0  # ink each side of the route's line


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


def ridden_ring(*, mpp: int) -> tuple[float, float]:
    """
    Size a ridden stop's ring so it shows outside the route's line.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[float, float]
        The ring's radius, `RIDDEN_REACH` beyond half the route's width
        and at least `RIDDEN_RING`, and its paper centre's radius, the
        same ink width inside it as the smallest ring has.
    """
    ring = max(RIDDEN_RING, ROUTE_STYLE[mpp][0] / 2 + RIDDEN_REACH)
    return ring, ring - (RIDDEN_RING - RIDDEN_HOLE)


def draw_ridden_stops(
    canvas: Canvas, *, base: MapBase, window: Window,
) -> None:
    """
    Draw each ridden stop as an ink ring with a paper centre.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key.
    window : Window
        The map's top-left corner in global pixels.
    """
    points = [
        (x - window[0], y - window[1])
        for x, y in (
            to_pixels(point=point, mpp=base.mpp, projection=base.projection)
            for point in base.stops
        )
    ]
    ring, hole = ridden_ring(mpp=base.mpp)
    canvas.dots(points=points, diameter=2 * ring, grey=INK)
    canvas.dots(points=points, diameter=2 * hole, grey=PAPER)
