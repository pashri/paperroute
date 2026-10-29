"""Where a map sits: the fitted zoom moved by the knob's step."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from paperroute.core.geo import Point
from paperroute.core.map_view import DEFAULT_ZOOM, MAP_HEIGHT, MapInputs
from paperroute.core.ridden import ridden_stops, stops_window
from paperroute.core.tiles import ZOOMS

FIT_MARGIN: Final[float] = 1.1  # the box, plus 10%
CREDIT_ROOM: Final[int] = 28  # pixels kept clear above the map's bottom
BANNER_ROOM: Final[int] = 34  # pixels a banner covers at the map's top
KEEP_IN: Final[int] = 24  # pixels the From is kept inside the map
SLEEP_ROOM: Final[int] = 56  # pixels the sleep band covers at the bottom


def stepped(*, zoom: int, step: int) -> int:
    """
    Move a fitted zoom by the knob's step.

    Parameters
    ----------
    zoom : int
        The fitted zoom, an index into `ZOOMS`.
    step : int
        Knob detents from the fit; negative is finer.

    Returns
    -------
    int
        The index moved by the step, clamped to `ZOOMS`.
    """
    return min(max(zoom + step, 0), len(ZOOMS) - 1)


def resting_zoom(*, step: int) -> int:
    """
    Give the zoom of a map with nothing to fit.

    Parameters
    ----------
    step : int
        Knob detents; negative is finer.

    Returns
    -------
    int
        `DEFAULT_ZOOM` moved by the step, clamped to `ZOOMS`.
    """
    return stepped(zoom=DEFAULT_ZOOM, step=step)


def banner_room(*, inputs: MapInputs) -> int:
    """
    Say how much of the map's top a banner covers.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    int
        Pixels; 0 with no banner.
    """
    return BANNER_ROOM if inputs.stop_view.banner else 0


def bottom_room(*, inputs: MapInputs) -> int:
    """
    Say how much of the map's bottom is kept clear.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    int
        Pixels: the sleep band's height asleep, else the corner boxes'.
    """
    return SLEEP_ROOM if inputs.asleep else CREDIT_ROOM


def fit_zoom(*, points: Sequence[Point], width: int) -> int:
    """
    Find the finest zoom whose map holds a set of points.

    Parameters
    ----------
    points : Sequence[Point]
        Points in metres.
    width : int
        Map width in pixels.

    Returns
    -------
    int
        The finest `ZOOMS` index whose `width` by `MAP_HEIGHT` window
        holds the points' box with a 10% margin, else the widest.
    """
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    across = (max(xs) - min(xs)) * FIT_MARGIN
    down = (max(ys) - min(ys)) * FIT_MARGIN
    return next(
        (
            index
            for index, mpp in enumerate(ZOOMS)
            if across <= width * mpp and down <= MAP_HEIGHT * mpp
        ),
        len(ZOOMS) - 1,
    )


def middle(*, ends: tuple[Point, Point]) -> Point:
    """
    Find the point halfway between two points.

    Parameters
    ----------
    ends : tuple[Point, Point]
        The two points.

    Returns
    -------
    Point
        The midpoint.
    """
    first, second = ends
    return (first[0] + second[0]) / 2, (first[1] + second[1]) / 2


def box_of(*, points: Sequence[Point]) -> tuple[Point, Point]:
    """
    Find the box around some points.

    Parameters
    ----------
    points : Sequence[Point]
        At least one point.

    Returns
    -------
    tuple[Point, Point]
        The smallest and largest corners.
    """
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return (min(xs), min(ys)), (max(xs), max(ys))


def roomy_zoom(
    *, box: tuple[Point, Point], width: int, top: int, bottom: int,
) -> int:
    """
    Find the finest zoom that holds a box and the map's covered edges.

    Parameters
    ----------
    box : tuple[Point, Point]
        Corners in metres.
    width : int
        Map width in pixels.
    top : int
        Pixels a banner covers at the top.
    bottom : int
        Pixels kept clear at the bottom.

    Returns
    -------
    int
        The fitted zoom, one wider when the box and the top and bottom
        rooms do not fit the map's height.
    """
    zoom = fit_zoom(points=box, width=width)
    if (box[1][1] - box[0][1]) + (bottom + top) * ZOOMS[zoom] > (
        MAP_HEIGHT * ZOOMS[zoom]
    ):
        zoom = min(zoom + 1, len(ZOOMS) - 1)
    return zoom


def fitted(
    *,
    points: Sequence[Point],
    width: int,
    top: int,
    step: int,
    keep: Point | None,
    bottom: int = CREDIT_ROOM,
) -> tuple[Point, int] | None:
    """
    Frame a set of points, moved by the knob's step.

    Parameters
    ----------
    points : Sequence[Point]
        Points to hold, in metres.
    width : int
        Map width in pixels.
    top : int
        Pixels a banner covers at the top.
    step : int
        Knob detents from the fit; negative is finer.
    keep : Point | None
        A point to keep `KEEP_IN` pixels inside the map whatever the
        zoom, or None.
    bottom : int
        Pixels kept clear at the bottom: the corner boxes, or the sleep
        band.

    Returns
    -------
    tuple[Point, int] | None
        The middle of the points' box, moved so the box clears the
        bottom room and the banner, then moved the least that keeps
        `keep` inside; and the stepped zoom. None with no points.
    """
    if not points:
        return None
    box = box_of(points=points)
    zoom = stepped(
        zoom=roomy_zoom(box=box, width=width, top=top, bottom=bottom),
        step=step,
    )
    mpp = ZOOMS[zoom]
    x, y = middle(ends=box)
    centre = (x, y + (top - bottom) * mpp / 2)
    if keep is not None:
        centre = kept(
            centre=centre,
            keep=keep,
            mpp=mpp,
            width=width,
            rooms=(top, bottom),
        )
    return centre, zoom


def kept(
    *,
    centre: Point,
    keep: Point,
    mpp: int,
    width: int,
    rooms: tuple[int, int],
) -> Point:
    """
    Move a centre the least that puts a point well inside the map.

    Parameters
    ----------
    centre : Point
        Map centre in metres.
    keep : Point
        The point to keep inside.
    mpp : int
        Metres per pixel.
    width : int
        Map width in pixels.
    rooms : tuple[int, int]
        Pixels covered at the top and kept clear at the bottom.

    Returns
    -------
    Point
        The centre, moved so `keep` is at least `KEEP_IN` pixels inside
        the sides and clear of both rooms.
    """
    top, bottom = rooms
    x = width / 2 + (keep[0] - centre[0]) / mpp
    y = MAP_HEIGHT / 2 - (keep[1] - centre[1]) / mpp
    right = min(max(x, KEEP_IN), width - KEEP_IN) - x
    down = min(max(y, top + KEEP_IN), MAP_HEIGHT - bottom - KEEP_IN) - y
    return (centre[0] - right * mpp, centre[1] + down * mpp)


def plain_frame(*, inputs: MapInputs) -> MapInputs:
    """
    Frame a plain pin's map on its next trip.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs, centred on the From.

    Returns
    -------
    MapInputs
        Fitted to the next departure's ridden stops and the From and To,
        with the From kept inside; the From at the resting zoom with
        fewer than two points; unchanged with no From point.
    """
    origin, end = inputs.ends
    if origin is None:
        return inputs
    points = (
        *trip_stops(inputs=inputs),
        origin,
        *(() if end is None else (end,)),
    )
    if len(set(points)) < 2:
        return replace(
            inputs, centre=origin, zoom=resting_zoom(step=inputs.step),
        )
    centre, zoom = fitted(
        points=points,
        width=inputs.width,
        top=banner_room(inputs=inputs),
        step=inputs.step,
        keep=origin,
        bottom=bottom_room(inputs=inputs),
    ) or (origin, inputs.zoom)
    return replace(inputs, centre=centre, zoom=zoom)


def trip_stops(*, inputs: MapInputs) -> tuple[Point, ...]:
    """
    List the ridden stops of a pin's next departure.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    tuple[Point, ...]
        The stops of the first departure leaving now or later, else of
        the first; none without departures or before its pattern loads.
    """
    if not inputs.departures:
        return ()
    upcoming = next(
        (
            departure
            for departure in inputs.departures
            if departure.departs >= inputs.now
        ),
        inputs.departures[0],
    )
    return ridden_stops(
        departure=upcoming,
        pattern=inputs.patterns.get(upcoming.trip_id),
        window=stops_window(destination=inputs.stop_view.destination),
    )
