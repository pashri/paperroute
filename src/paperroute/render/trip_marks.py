"""Draw a connection's marks: leg 2, the walk, the change and the ends."""

from collections.abc import Sequence
from typing import Final

from paperroute.render.map_marks import ROUTE_STYLE, Window, draw_stop
from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import MapBase, screen_point
from paperroute_transit.core.tiles import to_pixels

CASING: Final[int] = 4  # extra paper width under leg 2
LEG_DASH: Final[tuple[float, float]] = (12.0, 7.0)
WALK_CASING: Final[float] = 5.0
WALK_WIDTH: Final[float] = 2.5
WALK_DASH: Final[tuple[float, float]] = (2.0, 4.0)
SQUARE: Final[int] = 8  # half the change square's side
SQUARE_EDGE: Final[int] = 3  # paper round the square
END_DISC: Final[int] = 12
END_EDGE: Final[int] = 3  # paper round the To's disc


def on_map(
    *, points: Sequence[Point], base: MapBase, window: Window,
) -> list[Point]:
    """
    Convert metres to map pixels from the map's corner.

    Parameters
    ----------
    points : Sequence[Point]
        Points in metres.
    base : MapBase
        The base map key, for its zoom and projection.
    window : Window
        The map's top-left corner in global pixels.

    Returns
    -------
    list[Point]
        Pixels from the map's left and top edges.
    """
    found = [
        to_pixels(point=point, mpp=base.mpp, projection=base.projection)
        for point in points
    ]
    return [(x - window[0], y - window[1]) for x, y in found]


def draw_change(canvas: Canvas, *, at: Point) -> None:
    """
    Draw the change as an ink square edged with paper.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : Point
        Its centre in map pixels.
    """
    x, y = at
    edge = SQUARE + SQUARE_EDGE
    canvas.rect(box=(x - edge, y - edge, x + edge, y + edge), grey=PAPER)
    canvas.rect(
        box=(x - SQUARE, y - SQUARE, x + SQUARE, y + SQUARE), grey=INK,
    )


def draw_end(canvas: Canvas, *, at: Point) -> None:
    """
    Draw the To as an ink disc edged with paper.

    Parameters
    ----------
    canvas : Canvas
        Target.
    at : Point
        Its centre in map pixels.
    """
    x, y = at
    edge = END_DISC + END_EDGE
    canvas.ellipse(box=(x - edge, y - edge, x + edge, y + edge), grey=PAPER)
    canvas.ellipse(
        box=(x - END_DISC, y - END_DISC, x + END_DISC, y + END_DISC),
        grey=INK,
    )


def cased_line(
    canvas: Canvas,
    *,
    points: Sequence[Point],
    widths: tuple[float, float],
    dash: tuple[float, float],
) -> None:
    """
    Draw a dashed ink line over a wider paper casing.

    Parameters
    ----------
    canvas : Canvas
        Target.
    points : Sequence[Point]
        The line in map pixels.
    widths : tuple[float, float]
        The line's width and its casing's.
    dash : tuple[float, float]
        The dash pattern.
    """
    width, casing = widths
    canvas.line(points=points, width=casing, grey=PAPER)
    canvas.line(points=points, width=width, grey=INK, dash=dash)


def draw_trip_legs(canvas: Canvas, *, base: MapBase, window: Window) -> None:
    """
    Draw the dashed rides on a paper casing, and the dotted walks.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key; nothing is drawn without trip marks.
    window : Window
        The map's top-left corner in global pixels.
    """
    trip = base.trip
    if trip is None:
        return
    width = ROUTE_STYLE[base.mpp][0]
    for line in trip.second:
        cased_line(
            canvas,
            points=on_map(points=line, base=base, window=window),
            widths=(width, width + CASING),
            dash=LEG_DASH,
        )
    walks = () if trip.walk is None else (trip.walk,)
    for ends in (*walks, *trip.others):
        cased_line(
            canvas,
            points=on_map(points=ends, base=base, window=window),
            widths=(WALK_WIDTH, WALK_CASING),
            dash=WALK_DASH,
        )


def draw_trip_ends(canvas: Canvas, *, base: MapBase, window: Window) -> None:
    """
    Draw each change as a square and the To as a disc.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key; nothing is drawn without trip marks.
    window : Window
        The map's top-left corner in global pixels.
    """
    trip = base.trip
    if trip is None:
        return
    changes = () if trip.change is None else (trip.change,)
    others = tuple(
        ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in trip.others
    )
    for change in on_map(
        points=(*changes, *others), base=base, window=window,
    ):
        draw_change(canvas, at=change)
    if trip.end is not None:
        (end,) = on_map(points=(trip.end,), base=base, window=window)
        draw_end(canvas, at=end)


def draw_trip_lines(canvas: Canvas, *, base: MapBase, window: Window) -> None:
    """
    Draw leg 2, the walk, the change and the To.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key; nothing is drawn without trip marks.
    window : Window
        The map's top-left corner in global pixels.
    """
    draw_trip_legs(canvas, base=base, window=window)
    draw_trip_ends(canvas, base=base, window=window)


def draw_trip_points(canvas: Canvas, *, base: MapBase) -> None:
    """
    Draw the From marker at the trip's origin, below the header.

    Parameters
    ----------
    canvas : Canvas
        Target, the whole frame.
    base : MapBase
        The base map key; nothing is drawn without a centre or an origin.
    """
    trip = base.trip
    if base.centre is None or trip is None or trip.origin is None:
        return
    x, y = screen_point(
        centre=base.centre,
        mpp=base.mpp,
        point=trip.origin,
        projection=base.projection,
        width=base.width,
    )
    draw_stop(canvas, at=(x, y + HEADER))
