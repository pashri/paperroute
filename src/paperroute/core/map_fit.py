"""Where a map sits: the fitted zoom moved by the knob's step."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import Final

from paperroute.core.map_view import DEFAULT_ZOOM, MAP_HEIGHT, MapInputs
from paperroute_transit.core.geo import Point
from paperroute_transit.core.ridden import ridden_stops, stops_window
from paperroute_transit.core.tiles import ZOOMS

CREDIT_ROOM: Final[int] = 28  # pixels kept clear above the map's bottom
BANNER_ROOM: Final[int] = 34  # pixels a banner covers at the map's top
KEEP_IN: Final[int] = 24  # pixels the From is kept inside the map
SLEEP_BAND: Final[int] = 56  # pixels the sleep band covers at the bottom
SLEEP_ROOM: Final[int] = SLEEP_BAND  # the margin clears a To disc above it


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
        Pixels; 0 with no banner, and asleep, where none is drawn.
    """
    shown = inputs.stop_view.banner and not inputs.asleep
    return BANNER_ROOM if shown else 0


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


def holds_box(
    *,
    box: tuple[Point, Point],
    mpp: int,
    width: int,
    rooms: tuple[int, int] = (0, 0),
) -> bool:
    """
    Say whether a map at a zoom holds a box with room round it.

    Parameters
    ----------
    box : tuple[Point, Point]
        Corners in metres.
    mpp : int
        Metres per pixel.
    width : int
        Map width in pixels.
    rooms : tuple[int, int]
        Pixels covered at the top and kept clear at the bottom.

    Returns
    -------
    bool
        True when the box fits with `KEEP_IN` pixels on every side,
        inside the rooms.
    """
    (left, low), (right, high) = box
    tall = MAP_HEIGHT - sum(rooms) - 2 * KEEP_IN
    return (
        right - left <= (width - 2 * KEEP_IN) * mpp
        and high - low <= tall * mpp
    )


def fit_zoom(
    *,
    points: Sequence[Point],
    width: int,
    rooms: tuple[int, int] = (0, 0),
) -> int:
    """
    Find the finest zoom whose map holds a set of points.

    Parameters
    ----------
    points : Sequence[Point]
        Points in metres.
    width : int
        Map width in pixels.
    rooms : tuple[int, int]
        Pixels covered at the top and kept clear at the bottom.

    Returns
    -------
    int
        The finest `ZOOMS` index whose map holds the points' box as
        `holds_box`, else the widest.
    """
    box = box_of(points=points)
    return next(
        (
            index
            for index, mpp in enumerate(ZOOMS)
            if holds_box(box=box, mpp=mpp, width=width, rooms=rooms)
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


def narrowed(
    *,
    box: tuple[Point, Point],
    inner: tuple[Point, Point],
    fits: Callable[..., bool],
) -> tuple[Point, Point]:
    """
    Swap in an inner box along each axis the box overflows.

    Parameters
    ----------
    box : tuple[Point, Point]
        Corners in metres.
    inner : tuple[Point, Point]
        Corners of the box to fall back to.
    fits : Callable[..., bool]
        `holds_box` with all but the box given.

    Returns
    -------
    tuple[Point, Point]
        The box's sides along each axis it fits, the inner box's along
        each it does not.
    """
    (left, low), (right, high) = box
    across = box if fits(box=((left, 0.0), (right, 0.0))) else inner
    down = box if fits(box=((0.0, low), (0.0, high))) else inner
    return (across[0][0], down[0][1]), (across[1][0], down[1][1])


def fitted(
    *,
    points: Sequence[Point],
    width: int,
    top: int,
    step: int,
    keep: Point | None,
    bottom: int = CREDIT_ROOM,
    ends: Sequence[Point] = (),
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
    ends : Sequence[Point]
        The From and To, framed on their own along an axis the points'
        box overflows.

    Returns
    -------
    tuple[Point, int] | None
        The middle of the box, moved so it clears the bottom room and
        the banner, then moved the least that keeps `keep` inside; and
        the stepped zoom. None with no points.
    """
    if not points:
        return None
    rooms = (top, bottom)
    box = box_of(points=points)
    zoom = stepped(
        zoom=fit_zoom(points=box, width=width, rooms=rooms), step=step,
    )
    mpp = ZOOMS[zoom]
    if ends:
        box = narrowed(
            box=box,
            inner=box_of(points=ends),
            fits=partial(holds_box, mpp=mpp, width=width, rooms=rooms),
        )
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


def known(*, points: Sequence[Point | None]) -> tuple[Point, ...]:
    """
    Drop the missing points.

    Parameters
    ----------
    points : Sequence[Point | None]
        Points, None where missing.

    Returns
    -------
    tuple[Point, ...]
        The points that are not None, in order.
    """
    return tuple(point for point in points if point is not None)


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
        or to the From and To alone when the stops do not fit, with the
        From kept inside; the From at the resting zoom with
        fewer than two points; unchanged with no From point.
    """
    origin = inputs.ends[0]
    if origin is None:
        return inputs
    points = plain_points(inputs=inputs)
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
        ends=known(points=inputs.ends),
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



def plain_points(*, inputs: MapInputs) -> tuple[Point, ...]:
    """
    List the points a plain pin's map holds.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    tuple[Point, ...]
        The next departure's ridden stops, then the From and the To
        where they have points.
    """
    return (
        *trip_stops(inputs=inputs),
        *known(points=inputs.ends),
    )


def steady_frame(
    *,
    inputs: MapInputs,
    held: tuple[Point, int] | None,
    held_ends: tuple[Point | None, Point | None] | None = None,
) -> MapInputs:
    """
    Frame a plain pin's map, keeping the frame on screen while it fits.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs, centred on the From.
    held : tuple[Point, int] | None
        The centre and zoom the map is drawn at now, or None.
    held_ends : tuple[Point | None, Point | None] | None
        The From and To points of the pin the held frame was drawn for.

    Returns
    -------
    MapInputs
        The held frame while it is for the same pin, has the fitted zoom
        and still holds the next trip with the From well inside; else as
        `plain_frame`.
    """
    fit = plain_frame(inputs=inputs)
    if held is None or held[1] != fit.zoom or held_ends != inputs.ends:
        return fit
    if not holds(inputs=inputs, frame=held):
        return fit
    return replace(fit, centre=held[0])


def holds(*, inputs: MapInputs, frame: tuple[Point, int]) -> bool:
    """
    Say whether a frame still shows a plain pin's trip.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    frame : tuple[Point, int]
        A map centre and zoom.

    Returns
    -------
    bool
        True when the From sits `KEEP_IN` pixels inside the map and
        every point of `plain_points` is clear of the banner and the
        bottom room.
    """
    centre, zoom = frame
    mpp = ZOOMS[zoom]
    rooms = (banner_room(inputs=inputs), bottom_room(inputs=inputs))
    origin = inputs.ends[0]
    if origin is None or centre != kept(
        centre=centre, keep=origin, mpp=mpp, width=inputs.width, rooms=rooms,
    ):
        return False
    pixels = [
        (
            inputs.width / 2 + (x - centre[0]) / mpp,
            MAP_HEIGHT / 2 - (y - centre[1]) / mpp,
        )
        for x, y in plain_points(inputs=inputs)
    ]
    return all(
        0 <= x <= inputs.width and rooms[0] <= y <= MAP_HEIGHT - rooms[1]
        for x, y in pixels
    )
