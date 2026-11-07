"""The Map of a connection pin: its rides, walks and chains."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Final

from paperroute.core.chains import Chain
from paperroute.core.connection import chain_legs
from paperroute.core.connection_view import PairRow, chain_rows, no_pair_text
from paperroute.core.map_fit import (
    banner_room,
    bottom_room,
    fitted,
    known,
    middle,
    resting_zoom,
)
from paperroute.core.map_view import (
    MAP_WIDTH,
    MapInputs,
    MapView,
    TripMarks,
    build_map_view,
    pin_trips,
)
from paperroute.core.rides import Ride, rides_of
from paperroute.core.saved import ConnectionMap
from paperroute_transit.core.geo import (
    Point,
    Shape,
    point_along,
    stop_distances,
)
from paperroute_transit.core.model import Change, Departure, Pin, StopPlace
from paperroute_transit.core.ridden import SECOND_RIDE_WINDOW, route_stops
from paperroute_transit.core.tiles import ZOOMS

SIDEBAR_PAIRS: Final[int] = 4
PANES_WIDTH: Final[int] = 800  # the whole frame, shared by change panes

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
        The lines of the first, third, ... rides, drawn solid, and of the
        second, fourth, ... rides, drawn dashed; empty when not drawn.
    """
    if chain is None:
        return (), ()
    return alternate(
        lines=ride_lines(rides=rides_of(chain=chain, pin=pin), shapes=shapes),
    )


def alternate(
    *, lines: Sequence[tuple[Line, ...]],
) -> tuple[tuple[Line, ...], tuple[Line, ...]]:
    """
    Split each ride's lines into solid and dashed by ride.

    Parameters
    ----------
    lines : Sequence[tuple[Line, ...]]
        Each ride's lines in order.

    Returns
    -------
    tuple[tuple[Line, ...], tuple[Line, ...]]
        Even rides' lines, then odd rides' lines.
    """
    return (
        tuple(line for each in lines[::2] for line in each),
        tuple(line for each in lines[1::2] for line in each),
    )


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
        For the whole trip, every leg of the listed chains; for the
        start, their first legs; for the first leg, the hero's first leg.
        Zoomed on the changes, every leg of the hero chain.
    """
    listed = chains[:SIDEBAR_PAIRS]
    if mode is ConnectionMap.WHOLE:
        legs = [leg for chain in listed for leg in chain_legs(chain=chain)]
    elif mode is ConnectionMap.START:
        legs = [chain_legs(chain=chain)[0] for chain in listed]
    elif mode is ConnectionMap.FIRST_LEG:
        legs = list(chain_legs(chain=chains[0])[:1]) if chains else []
    else:
        legs = list(chain_legs(chain=chains[0])) if chains else []
    return pin_trips(departures=legs)


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
        The fit of the hero's legs and the four stops, as `fit_frame`,
        keeping the From; of the From and To when the legs do not fit.
    """
    hero = chains[0] if chains else None
    first, second = leg_lines(pin=pin, chain=hero, shapes=inputs.shapes)
    points = trip_points(pin=pin, chain=hero, lines=(*first, *second))
    return fit_frame(
        inputs=inputs,
        points=points,
        keep=pin.origin.point,
        ends=known(points=inputs.ends),
    )


def first_leg_frame(
    *, inputs: MapInputs, pin: Pin, chains: Sequence[Chain],
) -> tuple[Point | None, int]:
    """
    Frame the hero's first ride, from the From to the first change.

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
        The fit of the first ride's line and its two stops, as
        `fit_frame` keeping the From; for a direct chain, that is the
        whole trip.
    """
    if not chains:
        return fit_frame(inputs=inputs, points=(), keep=None)
    ride = rides_of(chain=chains[0], pin=pin)[0]
    lines = cut_leg(
        departure=ride.item.departure,
        ends=(ride.start, ride.end),
        shapes=inputs.shapes,
    )
    stops = (ride.start.point, ride.end.point)
    return fit_frame(
        inputs=inputs,
        points=(
            *(point for line in lines for point in line),
            *(point for point in stops if point is not None),
        ),
        keep=pin.origin.point,
    )


def fit_frame(
    *,
    inputs: MapInputs,
    points: Sequence[Point],
    keep: Point | None,
    ends: Sequence[Point] = (),
) -> tuple[Point | None, int]:
    """
    Frame a set of points.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    points : Sequence[Point]
        Points to hold.
    keep : Point | None
        A point kept on the map when the points do not fit at the
        widest zoom, or None.
    ends : Sequence[Point]
        The From and To, framed alone when the points do not fit.

    Returns
    -------
    tuple[Point | None, int]
        The middle of the points' box, moved so the box clears the map's
        credit and any banner, then moved the least that keeps `keep`
        inside, and the finest zoom that holds it with that room, moved
        by the knob's step; the From at the resting zoom with no point
        at all.
    """
    found = fitted(
        points=points,
        width=inputs.width,
        top=banner_room(inputs=inputs),
        step=inputs.step,
        keep=keep,
        bottom=bottom_room(inputs=inputs),
        ends=ends,
    )
    if found is None:
        return inputs.centre, resting_zoom(step=inputs.step)
    return found


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
        The whole trip, the first leg or the hero's first change, each
        fitted as `fit_frame` with the knob's step. Zoomed on the start,
        the From at the finest zoom whatever the knob says, centred in
        the map below any banner.
    """
    if mode is ConnectionMap.WHOLE:
        return whole_frame(inputs=inputs, pin=pin, chains=chains)
    if mode is ConnectionMap.FIRST_LEG:
        return first_leg_frame(inputs=inputs, pin=pin, chains=chains)
    if mode is ConnectionMap.CHANGES:
        return change_frame(inputs=inputs, pin=pin, chains=chains)
    origin = pin.origin.point
    if origin is None:
        return inputs.centre, 0
    x, y = origin
    return (x, y + banner_room(inputs=inputs) * ZOOMS[0] / 2), 0


def change_frame(
    *, inputs: MapInputs, pin: Pin, chains: Sequence[Chain],
) -> tuple[Point | None, int]:
    """
    Frame the hero's first change.

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
        The fit of `change_points` for the ride into the hero's first
        change and the ride out of it, as `fit_frame` keeping the
        change; the first leg's
        frame with no change; the From at the resting zoom with no chain.
    """
    if not chains:
        return inputs.centre, resting_zoom(step=inputs.step)
    rides = rides_of(chain=chains[0], pin=pin)
    changed = [index for index, ride in enumerate(rides) if ride.change]
    if not changed:
        return first_leg_frame(inputs=inputs, pin=pin, chains=chains)
    pair = rides[changed[0] - 1 : changed[0] + 1]
    return fit_frame(
        inputs=inputs,
        points=change_points(inputs=inputs, rides=pair),
        keep=pair[0].end.point,
    )


def change_points(
    *, inputs: MapInputs, rides: Sequence[Ride],
) -> tuple[Point, ...]:
    """
    List the points a change's map holds.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    rides : Sequence[Ride]
        The ride into the change and the ride out of it.

    Returns
    -------
    tuple[Point, ...]
        The first ride's ridden stops, the second's for its first
        `SECOND_RIDE_WINDOW`, the first ride's two ends, the second's
        start and the walk's ends. The second ride's far end is left out.
    """
    into, out = rides
    stops = (
        *route_stops(
            departures=(into.item.departure, into.last.departure),
            patterns=inputs.patterns,
            window=None,
        ),
        *route_stops(
            departures=(out.item.departure,),
            patterns=inputs.patterns,
            window=SECOND_RIDE_WINDOW,
        ),
    )
    walk = None if out.change is None else walk_ends(change=out.change)
    ends = (into.start.point, into.end.point, out.start.point, *(walk or ()))
    return (*stops, *(point for point in ends if point is not None))


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
        Which framing.

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


def panes_inputs(
    *,
    inputs: MapInputs,
    pin: Pin,
    chains: Sequence[Chain],
    mode: ConnectionMap,
) -> MapInputs:
    """
    Frame the area every Changes pane shows, for the area requests.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs, framed by `connection_inputs`.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains.
    mode : ConnectionMap
        Which framing.

    Returns
    -------
    MapInputs
        On the Map in Changes with a two-change hero, fitted to every
        change's `change_points` at the Map's width; else unchanged.
    """
    if mode is not ConnectionMap.CHANGES or inputs.width != MAP_WIDTH:
        return inputs
    rides = rides_of(chain=chains[0], pin=pin) if chains else ()
    changed = [index for index, ride in enumerate(rides) if ride.change]
    if len(changed) < 2:
        return inputs
    points = tuple(
        point
        for index in changed
        for point in change_points(
            inputs=inputs, rides=rides[index - 1 : index + 1],
        )
    )
    centre, zoom = fit_frame(inputs=inputs, points=points, keep=None)
    return replace(inputs, centre=centre, zoom=zoom)


def sidebar_rows(
    *, pin: Pin, chains: Sequence[Chain], now: datetime,
) -> tuple[PairRow, ...]:
    """
    Summarise the first chains for the sidebar.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains.
    now : datetime
        Current time.

    Returns
    -------
    tuple[PairRow, ...]
        Up to four rows whose detail is the journey time, then where it
        changes or that it is direct or stays on; the first spells out
        a single change.
    """
    rows = chain_rows(chains=chains[:SIDEBAR_PAIRS], pin=pin, now=now)
    return tuple(
        replace(
            row,
            detail=f'{row.minutes} min · ' + (
                row.detail if index else sidebar_detail(detail=row.detail)
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
        Which framing.

    Returns
    -------
    MapView
        The map with leg 1 as the route, leg 2, the walk and the ends as
        trip marks, and the pairs in the sidebar; its notice says why
        there is no chain, never that there is no direct trip.
    """
    framing = connection_inputs(
        inputs=inputs, pin=pin, chains=chains, mode=mode,
    )
    view = build_map_view(inputs=framing)
    hero = chains[0] if chains else None
    rides = () if hero is None else rides_of(chain=hero, pin=pin)
    route, trip = chain_marks(pin=pin, rides=rides, shapes=inputs.shapes)
    if hero is None:
        walk = walk_of(pin=pin, chain=None)
        trip = replace(
            trip,
            walk=walk,
            change=None if walk is None else middle(ends=walk),
        )
    return replace(
        view,
        base=replace(view.base, route=route, trip=trip),
        sidebar=(),
        pairs=sidebar_rows(pin=pin, chains=chains, now=inputs.now),
        empty=(
            view.empty
            if framing.centre is None
            else no_pair_text(stop_view=inputs.stop_view, chains=chains)
        ),
    )


def chain_marks(
    *, pin: Pin, rides: Sequence[Ride], shapes: Mapping[str, Shape],
) -> tuple[tuple[Line, ...], TripMarks]:
    """
    Draw rides as the route and trip marks.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    rides : Sequence[Ride]
        The rides drawn.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    tuple[tuple[Line, ...], TripMarks]
        The solid lines as the route, and the dashed lines, each change's
        walk and the ends as trip marks.
    """
    solid, dashed = alternate(lines=ride_lines(rides=rides, shapes=shapes))
    walks = [
        walk
        for ride in rides
        if ride.change is not None
        and (walk := walk_ends(change=ride.change)) is not None
    ]
    end = pin.destination
    return solid, TripMarks(
        origin=pin.origin.point,
        second=dashed,
        walk=walks[0] if walks else None,
        change=middle(ends=walks[0]) if walks else None,
        end=None if end is None else end.point,
        others=tuple(walks[1:]),
    )


def change_view(
    *,
    inputs: MapInputs,
    pin: Pin,
    chain: Chain,
    rides: Sequence[Ride],
) -> MapView:
    """
    Build one pane fitted on the change between two rides.

    Parameters
    ----------
    inputs : MapInputs
        The pane's inputs, its width set.
    pin : Pin
        Connection pin.
    chain : Chain
        The hero chain.
    rides : Sequence[Ride]
        The ride before the change and the ride after it.

    Returns
    -------
    MapView
        The pane fitted to `change_points` with the knob's step and the
        change kept on it, with both rides drawn and no sidebar.
    """
    centre, zoom = fit_frame(
        inputs=inputs,
        points=change_points(inputs=inputs, rides=rides),
        keep=rides[0].end.point,
    )
    legs = chain_legs(chain=chain)
    view = build_map_view(inputs=replace(
        inputs,
        departures=legs,
        centre=centre,
        zoom=zoom,
        vehicle_trips=pin_trips(departures=legs),
    ))
    drawn = (replace(rides[0], change=None), *rides[1:])
    route, trip = chain_marks(pin=pin, rides=drawn, shapes=inputs.shapes)
    return replace(
        view,
        base=replace(view.base, route=route, trip=trip),
        sidebar=(),
        empty=None if view.base.centre is not None else view.empty,
    )


def change_panes(
    *, inputs: MapInputs, pin: Pin, chain: Chain, mode: ConnectionMap,
) -> tuple[MapView, ...]:
    """
    Build the map panes the setting shows for a chain.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs as for a plain pin.
    pin : Pin
        Connection pin.
    chain : Chain
        The hero chain.
    mode : ConnectionMap
        Which framing.

    Returns
    -------
    tuple[MapView, ...]
        Two panes side by side, each half of `PANES_WIDTH`, for each
        change of a two-change chain; otherwise one pane of the map's own
        width: on the one change, or framed as `framed` says.
    """
    rides = rides_of(chain=chain, pin=pin)
    changed = [index for index, ride in enumerate(rides) if ride.change]
    if mode is not ConnectionMap.CHANGES or not changed:
        return (build_connection_map(
            inputs=inputs, pin=pin, chains=(chain,), mode=mode,
        ),)
    if len(changed) > 1:
        inputs = replace(inputs, width=PANES_WIDTH // len(changed))
    return tuple(
        change_view(
            inputs=inputs, pin=pin, chain=chain,
            rides=rides[index - 1 : index + 1],
        )
        for index in changed
    )
