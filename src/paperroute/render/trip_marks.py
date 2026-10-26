"""Draw a connection's marks: leg 2, the walk, the change and the ends."""

import math
from collections.abc import Sequence
from typing import Final

from paperroute.build.map_style import STATION_FACE
from paperroute.core.geo import Point
from paperroute.core.map_view import MapBase, screen_point
from paperroute.core.tiles import LabelKind, to_pixels
from paperroute.ports import TileRepository
from paperroute.render.canvas import INK, PAPER, Canvas
from paperroute.render.chrome import HEADER
from paperroute.render.map_marks import ROUTE_STYLE, draw_stop

Window = tuple[int, int]

CASING: Final[int] = 4  # extra paper width under leg 2
LEG_DASH: Final[tuple[float, float]] = (12.0, 7.0)
WALK_CASING: Final[float] = 5.0
WALK_WIDTH: Final[float] = 2.5
WALK_DASH: Final[tuple[float, float]] = (2.0, 4.0)
SQUARE: Final[int] = 8  # half the change square's side
SQUARE_EDGE: Final[int] = 3  # paper round the square
END_DISC: Final[int] = 8
END_EDGE: Final[int] = 3  # paper round the To's disc
NAME_REACH: Final[float] = 20.0  # pixels from the To to its station
NAME_HALO: Final[int] = 2


def on_map(
    *, points: Sequence[Point], mpp: int, window: Window,
) -> list[Point]:
    """
    Convert metres to map pixels from the map's corner.

    Parameters
    ----------
    points : Sequence[Point]
        Points in metres.
    mpp : int
        Metres per pixel.
    window : Window
        The map's top-left corner in global pixels.

    Returns
    -------
    list[Point]
        Pixels from the map's left and top edges.
    """
    found = [to_pixels(point=point, mpp=mpp) for point in points]
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


def draw_trip_legs(canvas: Canvas, *, base: MapBase, window: Window) -> None:
    """
    Draw leg 2 dashed on a paper casing, and the dotted walk.

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
        points = on_map(points=line, mpp=base.mpp, window=window)
        canvas.line(points=points, width=width + CASING, grey=PAPER)
        canvas.line(points=points, width=width, grey=INK, dash=LEG_DASH)
    if trip.walk is not None:
        walk = on_map(points=trip.walk, mpp=base.mpp, window=window)
        canvas.line(points=walk, width=WALK_CASING, grey=PAPER)
        canvas.line(points=walk, width=WALK_WIDTH, grey=INK, dash=WALK_DASH)


def draw_trip_ends(canvas: Canvas, *, base: MapBase, window: Window) -> None:
    """
    Draw the change as a square and the To as a disc.

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
    if trip.change is not None:
        (change,) = on_map(points=(trip.change,), mpp=base.mpp, window=window)
        draw_change(canvas, at=change)
    if trip.end is not None:
        (end,) = on_map(points=(trip.end,), mpp=base.mpp, window=window)
        draw_end(canvas, at=end)


def draw_end_name(
    canvas: Canvas, *, base: MapBase, window: Window, tiles: TileRepository,
) -> None:
    """
    Draw the To's station name again, over the trip's marks.

    Parameters
    ----------
    canvas : Canvas
        Target, the map alone.
    base : MapBase
        The base map key; nothing is drawn without a To.
    window : Window
        The map's top-left corner in global pixels.
    tiles : TileRepository
        Where the station labels come from.
    """
    trip = base.trip
    if trip is None or trip.end is None:
        return
    x, y = to_pixels(point=trip.end, mpp=base.mpp)
    near = [
        label
        for label in tiles.street_labels(
            zoom=base.mpp, box=(x, y, x, y), kind=LabelKind.STATION,
        )
        if math.dist(label.start, (x, y)) <= NAME_REACH
    ]
    if not near:
        return
    label = min(near, key=lambda item: math.dist(item.start, (x, y)))
    canvas.text(
        at=(label.x - window[0], label.y - window[1]),
        text=label.name,
        face=STATION_FACE,
        grey=INK,
        anchor='mm',
        halo=NAME_HALO,
    )


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
        centre=base.centre, mpp=base.mpp, point=trip.origin, width=base.width,
    )
    draw_stop(canvas, at=(x, y + HEADER))
