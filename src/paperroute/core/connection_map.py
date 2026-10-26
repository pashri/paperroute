"""The Map of a connection pin: both legs, the walk and the pairs."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Final

from paperroute.core.connection import pair_legs
from paperroute.core.connection_model import Pair
from paperroute.core.connection_view import PairRow, pair_rows
from paperroute.core.geo import Point, Shape, point_along, stop_distances
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MapInputs,
    MapView,
    TripMarks,
    build_map_view,
    pin_trips,
)
from paperroute.core.model import Departure, Pin, StopPlace
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


def walk_of(*, pin: Pin, pair: Pair | None) -> tuple[Point, Point] | None:
    """
    Find the two ends of a pair's walk.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    pair : Pair | None
        The hero pair, or None.

    Returns
    -------
    tuple[Point, Point] | None
        The alight and board points; None when staying on or either has
        no point.
    """
    change = pin.change
    if change is None or (pair is not None and pair.second is None):
        return None
    alight, board = change.alight.point, change.board.point
    if alight is None or board is None:
        return None
    return alight, board


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


def leg_lines(
    *, pin: Pin, pair: Pair | None, shapes: Mapping[str, Shape],
) -> tuple[tuple[Line, ...], tuple[Line, ...]]:
    """
    Cut the hero pair's two legs from their shapes.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    pair : Pair | None
        The hero pair, or None.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    tuple[tuple[Line, ...], tuple[Line, ...]]
        Leg 1's line and leg 2's, each empty when not drawn.
    """
    change = pin.change
    end = pin.destination or pin.origin
    if pair is None or change is None:
        return (), ()
    if pair.second is None:
        first = cut_leg(
            departure=pair.first.departure, ends=(pin.origin, end),
            shapes=shapes,
        )
        return first, ()
    return (
        cut_leg(
            departure=pair.first.departure,
            ends=(pin.origin, change.alight),
            shapes=shapes,
        ),
        cut_leg(
            departure=pair.second.departure,
            ends=(change.board, end),
            shapes=shapes,
        ),
    )


def trip_points(*, pin: Pin, lines: Sequence[Line]) -> tuple[Point, ...]:
    """
    Collect every point the whole trip covers.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    lines : Sequence[Line]
        The legs' lines.

    Returns
    -------
    tuple[Point, ...]
        The lines' points and the four stops' points.
    """
    change = pin.change
    places = [pin.origin, pin.destination]
    if change is not None:
        places += [change.alight, change.board]
    stops = tuple(
        place.point
        for place in places
        if place is not None and place.point is not None
    )
    return (*(point for line in lines for point in line), *stops)


def shown_trips(
    *, inputs: MapInputs, pairs: Sequence[Pair], mode: ConnectionMap,
) -> frozenset[str]:
    """
    Choose the trips whose vehicles are drawn.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    pairs : Sequence[Pair]
        Workable pairs.
    mode : ConnectionMap
        Which framing.

    Returns
    -------
    frozenset[str]
        For the whole trip, both legs of the listed pairs. Zoomed on the
        change, the hero's leg 1 until it reaches the change, then its
        leg 2.
    """
    if mode is ConnectionMap.WHOLE:
        return pin_trips(departures=[
            departure
            for pair in pairs[:SIDEBAR_PAIRS]
            for departure in pair_legs(pair=pair)
        ])
    if not pairs:
        return frozenset()
    hero = pairs[0]
    legs = pair_legs(pair=hero)
    arrived = hero.second is not None and inputs.now >= hero.first.arrives
    return pin_trips(departures=legs[1:] if arrived else legs[:1])


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
    *, inputs: MapInputs, pin: Pin, pairs: Sequence[Pair],
) -> tuple[Point | None, int]:
    """
    Frame the whole trip.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs.

    Returns
    -------
    tuple[Point | None, int]
        The middle of the box around the hero's legs and the four stops,
        moved so the box clears the map's credit and any banner, and the finest
        zoom that holds it with that room; the From and the knob's zoom
        with no point at all.
    """
    hero = pairs[0] if pairs else None
    first, second = leg_lines(pin=pin, pair=hero, shapes=inputs.shapes)
    points = trip_points(pin=pin, lines=(*first, *second))
    if not points:
        return inputs.centre, inputs.zoom
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    box = ((min(xs), min(ys)), (max(xs), max(ys)))
    zoom = fit_zoom(points=box, width=inputs.width)
    top = banner_room(inputs=inputs)
    if (box[1][1] - box[0][1]) + (CREDIT_ROOM + top) * ZOOMS[zoom] > (
        MAP_HEIGHT * ZOOMS[zoom]
    ):
        zoom = min(zoom + 1, len(ZOOMS) - 1)
    x, y = middle(ends=box)
    return (x, y + (top - CREDIT_ROOM) * ZOOMS[zoom] / 2), zoom


def framed(
    *, inputs: MapInputs, pin: Pin, pairs: Sequence[Pair], mode: ConnectionMap,
) -> tuple[Point | None, int]:
    """
    Choose the map's centre and zoom.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs.
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
        return whole_frame(inputs=inputs, pin=pin, pairs=pairs)
    walk = walk_of(pin=pin, pair=None)
    zoom = max(0, inputs.zoom - CHANGE_ZOOM_IN)
    if walk is None:
        return inputs.centre, zoom
    x, y = middle(ends=walk)
    return (x, y + banner_room(inputs=inputs) * ZOOMS[zoom] / 2), zoom


def connection_inputs(
    *, inputs: MapInputs, pin: Pin, pairs: Sequence[Pair], mode: ConnectionMap,
) -> MapInputs:
    """
    Frame the map on a connection.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs as for a plain pin.
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs in leg-1 departure order.
    mode : ConnectionMap
        Zoomed on the change, or the whole trip.

    Returns
    -------
    MapInputs
        The hero pair's legs as the departures, the framing's centre and
        zoom, and the trips whose vehicles are drawn.
    """
    centre, zoom = framed(inputs=inputs, pin=pin, pairs=pairs, mode=mode)
    return replace(
        inputs,
        departures=pair_legs(pair=pairs[0]) if pairs else (),
        centre=centre,
        zoom=zoom,
        vehicle_trips=shown_trips(inputs=inputs, pairs=pairs, mode=mode),
    )


def sidebar_rows(*, pin: Pin, pairs: Sequence[Pair]) -> tuple[PairRow, ...]:
    """
    Summarise the first pairs for the sidebar.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs.

    Returns
    -------
    tuple[PairRow, ...]
        Up to four rows whose detail is the journey time; the first adds
        where to change, or that you stay on.
    """
    rows = pair_rows(pairs=pairs[:SIDEBAR_PAIRS])
    board = '' if pin.change is None else pin.change.board.name
    return tuple(
        replace(
            row,
            detail=f'{row.minutes} min' if index else (
                f'{row.minutes} min · stay on'
                if row.onto is None
                else f'{row.minutes} min · change at {board}'
            ),
        )
        for index, row in enumerate(rows)
    )


def build_connection_map(
    *, inputs: MapInputs, pin: Pin, pairs: Sequence[Pair], mode: ConnectionMap,
) -> MapView:
    """
    Build the connection Map.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs as for a plain pin.
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs in leg-1 departure order.
    mode : ConnectionMap
        Zoomed on the change, or the whole trip.

    Returns
    -------
    MapView
        The map with leg 1 as the route, leg 2, the walk and the ends as
        trip marks, and the pairs in the sidebar.
    """
    view = build_map_view(inputs=connection_inputs(
        inputs=inputs, pin=pin, pairs=pairs, mode=mode,
    ))
    hero = pairs[0] if pairs else None
    first, second = leg_lines(pin=pin, pair=hero, shapes=inputs.shapes)
    walk = walk_of(pin=pin, pair=hero)
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
        pairs=sidebar_rows(pin=pin, pairs=pairs),
    )
