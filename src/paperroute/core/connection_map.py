"""The Map of a connection pin: both legs, the walk and the pairs."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Final

from paperroute.core.chains import Chain
from paperroute.core.connection import chain_legs
from paperroute.core.connection_view import PairRow, chain_rows
from paperroute.core.geo import Point, Shape, point_along, stop_distances
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MapInputs,
    MapView,
    TripMarks,
    build_map_view,
    pin_trips,
)
from paperroute.core.model import Change, Departure, Pin, StopPlace
from paperroute.core.rides import Ride, rides_of
from paperroute.core.saved import ConnectionMap
from paperroute.core.tiles import ZOOMS

CHANGE_ZOOM_IN: Final[int] = 1
FIT_MARGIN: Final[float] = 1.1  # the whole trip's box, plus 10%
SIDEBAR_PAIRS: Final[int] = 4
CREDIT_ROOM: Final[int] = 24  # pixels kept clear above the map's bottom
BANNER_ROOM: Final[int] = 34  # pixels a banner covers at the map's top

type Line = tuple[Point, ...]


def leg_line(*, shape: Shape, start: Point, end: Point) -> Line:
    """
    Cut a shape between the points nearest two stops.

    Parameters
    ----------
    shape : Shape
        The leg's shape.
    start : Point
        Stop boarded at.
    end : Point
        Stop left at.

    Returns
    -------
    Line
        The point along the shape nearest `start`, the shape's points
        between, then the point nearest `end`.
    """
    distances = stop_distances(shape=shape, stops=(start, end))
    first, last = distances[0], distances[-1]
    inside = tuple(
        point
        for point, along in zip(shape.points, shape.along, strict=True)
        if first < along < last
    )
    return (
        point_along(shape=shape, distance=first),
        *inside,
        point_along(shape=shape, distance=last),
    )


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


def walk_ends(*, change: Change) -> tuple[Point, Point] | None:
    """
    Find the two ends of a change's walk.

    Parameters
    ----------
    change : Change
        The change.

    Returns
    -------
    tuple[Point, Point] | None
        The alight and board points; None when either has no point.
    """
    alight, board = change.alight.point, change.board.point
    if alight is None or board is None:
        return None
    return alight, board


def hero_changes(*, pin: Pin, chain: Chain | None) -> tuple[Change, ...]:
    """
    List the changes the hero chain makes.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    chain : Chain | None
        The hero chain, or None.

    Returns
    -------
    tuple[Change, ...]
        The changes between its rides; with no chain, the first ticked
        way's changes; none for a direct chain or a stay on.
    """
    if chain is None:
        ways = [way for way in pin.ways if way.ticked]
        return ways[0].changes if ways else ()
    return tuple(
        ride.change
        for ride in rides_of(chain=chain, pin=pin)
        if ride.change is not None
    )


def walk_of(*, pin: Pin, chain: Chain | None) -> tuple[Point, Point] | None:
    """
    Find the two ends of the hero chain's first walk.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    chain : Chain | None
        The hero chain, or None.

    Returns
    -------
    tuple[Point, Point] | None
        The alight and board points of its first change; None without a
        change or a point.
    """
    changes = hero_changes(pin=pin, chain=chain)
    return walk_ends(change=changes[0]) if changes else None


def cut_leg(
    *,
    departure: Departure,
    ends: tuple[StopPlace, StopPlace],
    shapes: Mapping[str, Shape],
) -> tuple[Line, ...]:
    """
    Cut one leg's line from its shape.

    Parameters
    ----------
    departure : Departure
        The leg's departure.
    ends : tuple[StopPlace, StopPlace]
        Where the leg is boarded and left.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    tuple[Line, ...]
        The cut line, or nothing without a shape or a point.
    """
    shape = shapes.get(departure.shape_id or '')
    start, end = ends[0].point, ends[1].point
    if shape is None or start is None or end is None:
        return ()
    return (leg_line(shape=shape, start=start, end=end),)


def ride_lines(
    *, rides: Sequence[Ride], shapes: Mapping[str, Shape],
) -> tuple[tuple[Line, ...], ...]:
    """
    Cut each ride's line from its shape.

    Parameters
    ----------
    rides : Sequence[Ride]
        The rides.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    tuple[tuple[Line, ...], ...]
        Each ride's lines, empty when not drawn.
    """
    return tuple(
        cut_leg(
            departure=ride.item.departure,
            ends=(ride.start, ride.end),
            shapes=shapes,
        )
        for ride in rides
    )


def leg_lines(
    *, pin: Pin, chain: Chain | None, shapes: Mapping[str, Shape],
) -> tuple[tuple[Line, ...], tuple[Line, ...]]:
    """
    Cut the hero chain's rides from their shapes.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    chain : Chain | None
        The hero chain, or None.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    tuple[tuple[Line, ...], tuple[Line, ...]]
        The first ride's lines and every later ride's, each empty when
        not drawn.
    """
    if chain is None:
        return (), ()
    lines = ride_lines(rides=rides_of(chain=chain, pin=pin), shapes=shapes)
    return lines[0], tuple(line for each in lines[1:] for line in each)


def trip_points(
    *, pin: Pin, chain: Chain | None, lines: Sequence[Line],
) -> tuple[Point, ...]:
    """
    Collect every point the whole trip covers.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    chain : Chain | None
        The hero chain, or None.
    lines : Sequence[Line]
        The legs' lines.

    Returns
    -------
    tuple[Point, ...]
        The lines' points, the ends and each change's stops.
    """
    places = [pin.origin, pin.destination]
    for change in hero_changes(pin=pin, chain=chain):
        places += [change.alight, change.board]
    stops = tuple(
        place.point
        for place in places
        if place is not None and place.point is not None
    )
    return (*(point for line in lines for point in line), *stops)


def shown_trips(
    *, chains: Sequence[Chain], mode: ConnectionMap,
) -> frozenset[str]:
    """
    Choose the trips whose vehicles are drawn.

    Parameters
    ----------
    chains : Sequence[Chain]
        Workable chains.
    mode : ConnectionMap
        Which framing.

    Returns
    -------
    frozenset[str]
        For the whole trip, every leg of the listed chains. Zoomed on the
        changes, every leg of the hero chain.
    """
    if mode is ConnectionMap.WHOLE:
        return pin_trips(departures=[
            departure
            for chain in chains[:SIDEBAR_PAIRS]
            for departure in chain_legs(chain=chain)
        ])
    if not chains:
        return frozenset()
    return pin_trips(departures=chain_legs(chain=chains[0]))


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


def whole_frame(
    *, inputs: MapInputs, pin: Pin, chains: Sequence[Chain],
) -> tuple[Point | None, int]:
    """
    Frame the whole trip.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains.

    Returns
    -------
    tuple[Point | None, int]
        The middle of the box around the hero's legs and the four stops,
        moved so the box clears the map's credit and any banner, and the finest
        zoom that holds it with that room; the From and the knob's zoom
        with no point at all.
    """
    hero = chains[0] if chains else None
    first, second = leg_lines(pin=pin, chain=hero, shapes=inputs.shapes)
    points = trip_points(pin=pin, chain=hero, lines=(*first, *second))
    if not points:
        return inputs.centre, inputs.zoom
    box = box_of(points=points)
    top = banner_room(inputs=inputs)
    zoom = roomy_zoom(box=box, width=inputs.width, top=top)
    x, y = middle(ends=box)
    return (x, y + (top - CREDIT_ROOM) * ZOOMS[zoom] / 2), zoom


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


def roomy_zoom(*, box: tuple[Point, Point], width: int, top: int) -> int:
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

    Returns
    -------
    int
        The fitted zoom, one wider when the box and the credit and banner
        rooms do not fit the map's height.
    """
    zoom = fit_zoom(points=box, width=width)
    if (box[1][1] - box[0][1]) + (CREDIT_ROOM + top) * ZOOMS[zoom] > (
        MAP_HEIGHT * ZOOMS[zoom]
    ):
        zoom = min(zoom + 1, len(ZOOMS) - 1)
    return zoom


def framed(
    *,
    inputs: MapInputs,
    pin: Pin,
    chains: Sequence[Chain],
    mode: ConnectionMap,
) -> tuple[Point | None, int]:
    """
    Choose the map's centre and zoom.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains.
    mode : ConnectionMap
        Which framing.

    Returns
    -------
    tuple[Point | None, int]
        For the whole trip, the middle of its box at the zoom that holds
        it. Zoomed on the change, the walk's midpoint one zoom finer than
        the knob's, centred in the map below any banner. The From and
        the knob's zoom when neither applies.
    """
    if mode is ConnectionMap.WHOLE:
        return whole_frame(inputs=inputs, pin=pin, chains=chains)
    walk = walk_of(pin=pin, chain=None)
    zoom = max(0, inputs.zoom - CHANGE_ZOOM_IN)
    if walk is None:
        return inputs.centre, zoom
    x, y = middle(ends=walk)
    return (x, y + banner_room(inputs=inputs) * ZOOMS[zoom] / 2), zoom


def connection_inputs(
    *,
    inputs: MapInputs,
    pin: Pin,
    chains: Sequence[Chain],
    mode: ConnectionMap,
) -> MapInputs:
    """
    Frame the map on a connection.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs as for a plain pin.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains in first-leg departure order.
    mode : ConnectionMap
        Zoomed on the change, or the whole trip.

    Returns
    -------
    MapInputs
        The hero pair's legs as the departures, the framing's centre and
        zoom, and the trips whose vehicles are drawn.
    """
    centre, zoom = framed(
        inputs=inputs, pin=pin, chains=chains, mode=mode,
    )
    return replace(
        inputs,
        departures=chain_legs(chain=chains[0]) if chains else (),
        centre=centre,
        zoom=zoom,
        vehicle_trips=shown_trips(chains=chains, mode=mode),
    )


def sidebar_rows(
    *, pin: Pin, chains: Sequence[Chain],
) -> tuple[PairRow, ...]:
    """
    Summarise the first chains for the sidebar.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains.

    Returns
    -------
    tuple[PairRow, ...]
        Up to four rows whose detail is the journey time; the first adds
        where it changes, or that it is direct or stays on.
    """
    rows = chain_rows(chains=chains[:SIDEBAR_PAIRS], pin=pin)
    return tuple(
        replace(
            row,
            detail=f'{row.minutes} min' if index else (
                f'{row.minutes} min · {sidebar_detail(detail=row.detail)}'
            ),
        )
        for index, row in enumerate(rows)
    )


def sidebar_detail(*, detail: str) -> str:
    """
    Word a row's detail for the sidebar's first row.

    Parameters
    ----------
    detail : str
        The row's detail.

    Returns
    -------
    str
        'change at {stop}' for one change; the detail otherwise.
    """
    return f'change {detail}' if detail.startswith('at ') else detail


def build_connection_map(
    *,
    inputs: MapInputs,
    pin: Pin,
    chains: Sequence[Chain],
    mode: ConnectionMap,
) -> MapView:
    """
    Build the connection Map.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs as for a plain pin.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains in first-leg departure order.
    mode : ConnectionMap
        Zoomed on the change, or the whole trip.

    Returns
    -------
    MapView
        The map with leg 1 as the route, leg 2, the walk and the ends as
        trip marks, and the pairs in the sidebar.
    """
    view = build_map_view(inputs=connection_inputs(
        inputs=inputs, pin=pin, chains=chains, mode=mode,
    ))
    hero = chains[0] if chains else None
    first, second = leg_lines(pin=pin, chain=hero, shapes=inputs.shapes)
    walk = walk_of(pin=pin, chain=hero)
    end = pin.destination
    trip = TripMarks(
        origin=pin.origin.point,
        second=second,
        walk=walk,
        change=None if walk is None else middle(ends=walk),
        end=None if end is None else end.point,
    )
    return replace(
        view,
        base=replace(view.base, route=first, trip=trip),
        sidebar=(),
        pairs=sidebar_rows(pin=pin, chains=chains),
    )
