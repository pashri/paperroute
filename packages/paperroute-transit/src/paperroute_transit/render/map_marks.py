"""The map's fixed marks: line widths, ridden stop rings and the From."""

from collections.abc import Sequence
from typing import Final

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import MapBase
from paperroute_transit.core.tiles import to_pixels

Window = tuple[int, int]

SOLID_WIDTH: Final[dict[int, float]] = {
    3: 13.0,
    5: 13.0,
    8: 10.0,
    14: 7.0,
    25: 4.0,
    45: 3.0,
}
LINE_CASING: Final[dict[int, float]] = {
    3: 2.0,
    5: 2.0,
    8: 2.0,
    14: 2.0,
    25: 1.5,
    45: 1.5,
}
RING_EDGE_SOLID: Final[float] = 2.0
RING_EDGE_DASHED: Final[float] = 1.5
RING_REACH: Final[float] = 3.5  # ring radius beyond half the stroke
STOP_DISC: Final[float] = 16.0
STOP_RING: Final[float] = 12.0
STOP_RING_WIDTH: Final[float] = 4.0
STOP_DOT: Final[float] = 5.0
MARK_SCALE: Final[dict[int, float]] = {25: 0.85, 45: 0.75}


def mark_scale(*, mpp: int) -> float:
    """
    Give the size of the map's marks.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    float
        1 to 14 m/px, 0.85 at 25 and 0.75 at 45.
    """
    return MARK_SCALE.get(mpp, 1.0)


def draw_stop(canvas: Canvas, *, at: Point, scale: float = 1.0) -> None:
    """
    Draw the From stop marker: a paper disc, an ink ring and an ink dot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : Point
        The stop's centre on the canvas.
    scale : float
        Size of the marker, 1 for full size.
    """
    with canvas.scaled(at=at, factor=scale):
        draw_bullseye(canvas, at=at)


def draw_bullseye(canvas: Canvas, *, at: Point) -> None:
    """
    Draw a paper disc, an ink ring and an ink dot.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : Point
        The centre on the canvas.
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


def dash_width(*, mpp: int) -> float:
    """
    Give the width of a dashed line.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    float
        Half the solid width.
    """
    return SOLID_WIDTH[mpp] / 2


def dash_pattern(*, mpp: int) -> tuple[float, float]:
    """
    Give the dash and gap lengths of a dashed line.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[float, float]
        Dash and gap in pixels; shorter from 25 m/px.
    """
    return (14.0, 8.0) if mpp < 25 else (10.0, 6.0)


def ring_radius(*, mpp: int, solid: bool) -> float:
    """
    Size a ridden stop's ring so it shows outside its line.

    Parameters
    ----------
    mpp : int
        Metres per pixel.
    solid : bool
        True for a ring on a solid line, False on a dashed one.

    Returns
    -------
    float
        Half the line's width plus `RING_REACH`.
    """
    width = SOLID_WIDTH[mpp] if solid else dash_width(mpp=mpp)
    return width / 2 + RING_REACH


def draw_rings(
    canvas: Canvas,
    *,
    points: Sequence[Point],
    radius: float,
    edge: float,
) -> None:
    """
    Draw rings as ink discs with paper centres.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    points : Sequence[Point]
        Ring centres in map pixels.
    radius : float
        The ring's outer radius.
    edge : float
        The ink's width.
    """
    canvas.dots(points=points, diameter=2 * radius, grey=INK)
    canvas.dots(points=points, diameter=2 * (radius - edge), grey=PAPER)


def draw_ridden_stops(
    canvas: Canvas, *, base: MapBase, window: Window,
) -> None:
    """
    Draw the rings on dashed lines, then the rings on solid lines.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key.
    window : Window
        The map's top-left corner in global pixels.
    """
    for stops, solid, edge in (
        (base.dashed_stops, False, RING_EDGE_DASHED),
        (base.stops, True, RING_EDGE_SOLID),
    ):
        points = [
            (x - window[0], y - window[1])
            for x, y in (
                to_pixels(
                    point=point, mpp=base.mpp, projection=base.projection,
                )
                for point in stops
            )
        ]
        draw_rings(
            canvas,
            points=points,
            radius=ring_radius(mpp=base.mpp, solid=solid),
            edge=edge,
        )
