"""The route lines the Map draws."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import tzinfo
from math import dist

from paperroute_transit.core.geo import Point, Shape, nearest_along
from paperroute_transit.core.map_line import MapLine
from paperroute_transit.core.map_view import (
    MapInputs,
    MapView,
    build_map_view,
    screen_point,
    visible,
)
from paperroute_transit.core.model import Departure, service_seconds
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.strip_times import from_call
from paperroute_transit.core.tiles import ZOOMS


def map_line_key(*, departure: Departure) -> str:
    """
    Give the line a departure draws on the Map.

    Parameters
    ----------
    departure : Departure
        The departure.

    Returns
    -------
    str
        The route on the road; on rail, the feed route ID, else the route.
    """
    if departure.facts.road:
        return departure.route
    return departure.facts.route_id or departure.route


def first_per_line(
    *, departures: Sequence[Departure],
) -> tuple[Departure, ...]:
    """
    Keep the first departure of each line.

    Parameters
    ----------
    departures : Sequence[Departure]
        The departures, in order.

    Returns
    -------
    tuple[Departure, ...]
        The first of each `map_line_key`, in order.
    """
    seen: dict[str, Departure] = {}
    for found in departures:
        seen.setdefault(map_line_key(departure=found), found)
    return tuple(seen.values())


@dataclass(frozen=True)
class LineChoice:
    """What a plain pin draws besides its own departures.

    Attributes
    ----------
    station : str
        The From's station: its parent station, or the From itself.
    extras : tuple[Departure, ...]
        The day's other lines, one departure each.
    hidden : frozenset[str]
        Kinds with no line and no rings.
    """

    station: str = ''
    extras: tuple[Departure, ...] = ()
    hidden: frozenset[str] = frozenset()


def line_shape(
    *,
    departure: Departure,
    patterns: Mapping[str, TripPattern | None],
    shapes: Mapping[str, Shape],
) -> Shape | None:
    """
    Find the shape a departure's line follows.

    Parameters
    ----------
    departure : Departure
        The departure.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    Shape | None
        The loaded pattern's shape when that is loaded too, else the
        departure's own; None when neither is loaded.
    """
    pattern = patterns.get(departure.trip_id)
    if pattern is not None and pattern.shape_id in shapes:
        return shapes[pattern.shape_id or '']
    return shapes.get(departure.shape_id or '')


def arrival_along(
    *,
    departure: Departure,
    pattern: TripPattern,
    along: Sequence[float],
    first: int,
    end: Point | None,
    zone: tzinfo,
) -> float | None:
    """
    Find how far along the shape a departure arrives.

    Parameters
    ----------
    departure : Departure
        The departure.
    pattern : TripPattern
        Its pattern.
    along : Sequence[float]
        Metres along the shape at each of the pattern's calls.
    first : int
        The index of the call it boards at.
    end : Point | None
        The Destination's point, if known.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    float | None
        Metres along at the later call at its arrival time nearest the
        Destination, or the last such call with no Destination point;
        None when there is none.
    """
    arrives = service_seconds(
        day=departure.service_day, moment=departure.arrives, zone=zone,
    )
    matches = [
        index
        for index in range(first + 1, len(pattern.stops))
        if pattern.stops[index].arrives == arrives
    ]
    if not matches:
        return None
    gaps = {
        index: dist(point, end)
        for index in matches
        if end is not None
        and (point := pattern.stops[index].point) is not None
    }
    if not gaps:
        return along[matches[-1]]
    return along[min(gaps, key=lambda index: gaps[index])]


def ride_span(
    *,
    departure: Departure,
    pattern: TripPattern | None,
    shape: Shape,
    station: str,
    ends: tuple[Point | None, Point | None],
    zone: tzinfo,
) -> tuple[float, float] | None:
    """
    Find the stretch of a shape a departure is ridden over.

    Parameters
    ----------
    departure : Departure
        The departure.
    pattern : TripPattern | None
        Its pattern, if loaded.
    shape : Shape
        The shape it follows.
    station : str
        The From's station.
    ends : tuple[Point | None, Point | None]
        The pin's From and To points.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    tuple[float, float] | None
        Metres along the shape at boarding and at alighting, or at the
        shape's end with no To; the shape's nearest points to the ends
        when the pattern is not measured or has no call at the From.
        None with no From point.
    """
    origin, end = ends
    if origin is None:
        return None
    index = (
        None
        if pattern is None or pattern.along is None
        else from_call(
            zone=zone,
            stations={},
            origin=station,
            pattern=pattern,
            departure=departure,
        )
    )
    if pattern is None or pattern.along is None or index is None:
        start = nearest_along(shape=shape, point=origin)
        stop = (
            shape.along[-1]
            if end is None
            else nearest_along(shape=shape, point=end)
        )
        return start, stop
    if end is None:
        return pattern.along[index], shape.along[-1]
    found = arrival_along(
        departure=departure,
        pattern=pattern,
        along=pattern.along,
        first=index,
        end=end,
        zone=zone,
    )
    stop = nearest_along(shape=shape, point=end) if found is None else found
    return pattern.along[index], stop


def drawn_key(*, inputs: MapInputs, departure: Departure) -> str:
    """
    Give the key of the shape a departure draws.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    departure : Departure
        The departure.

    Returns
    -------
    str
        The drawn shape's ID, or the trip's own ID when no shape is
        loaded for it.
    """
    shape = line_shape(
        departure=departure, patterns=inputs.patterns, shapes=inputs.shapes,
    )
    return departure.trip_id if shape is None else shape.shape_id


def drawn_lines(
    *, inputs: MapInputs, choice: LineChoice,
) -> tuple[tuple[Departure, ...], tuple[Departure, ...]]:
    """
    Choose the departures drawn solid and those drawn dashed.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    choice : LineChoice
        The kinds left out and the day's other lines.

    Returns
    -------
    tuple[tuple[Departure, ...], tuple[Departure, ...]]
        With no Destination, the first departure of each line, then the
        extras of other lines; with one, the first departure of each
        shape and no extras. Hidden kinds are left out.
    """
    shown = tuple(
        found
        for found in inputs.departures
        if found.facts.kind not in choice.hidden
    )
    if inputs.stop_view.destination is not None:
        first = {
            drawn_key(inputs=inputs, departure=found): found
            for found in reversed(shown)
        }
        return tuple(
            found
            for found in shown
            if first[drawn_key(inputs=inputs, departure=found)] is found
        ), ()
    solid = first_per_line(departures=shown)
    keys = {map_line_key(departure=found) for found in solid}
    return solid, tuple(
        found
        for found in choice.extras
        if found.facts.kind not in choice.hidden
        and map_line_key(departure=found) not in keys
    )


def plain_lines(
    *, inputs: MapInputs, choice: LineChoice,
) -> tuple[MapLine, ...]:
    """
    Build the lines a plain pin draws.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    choice : LineChoice
        The kinds left out and the day's other lines.

    Returns
    -------
    tuple[MapLine, ...]
        One line per drawn departure with a loaded shape, solid over its
        ride, then one dashed whole per extra. A line with no ride span
        is dashed whole.
    """
    solid, dashed = drawn_lines(inputs=inputs, choice=choice)
    lines: list[MapLine] = []
    for found in (*solid, *dashed):
        shape = line_shape(
            departure=found, patterns=inputs.patterns, shapes=inputs.shapes,
        )
        if shape is None:
            continue
        span = None if found in dashed else ride_span(
            departure=found,
            pattern=inputs.patterns.get(found.trip_id),
            shape=shape,
            station=choice.station,
            ends=inputs.ends,
            zone=inputs.zone,
        )
        lines.append(MapLine(shape_id=shape.shape_id, solid=span, shape=shape))
    return tuple(lines)


def ring_points(
    *,
    inputs: MapInputs,
    lines: Sequence[MapLine],
    departures: Sequence[Departure],
) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    """
    Place the rings on the stops the drawn lines call at.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    lines : Sequence[MapLine]
        The lines drawn.
    departures : Sequence[Departure]
        The departures whose patterns are ringed.

    Returns
    -------
    tuple[tuple[Point, ...], tuple[Point, ...]]
        The rings on solid stretches, then those on dashes, each in
        first-seen order. A stop on both is solid only; passed stops and
        stops off the map are left out.
    """
    centre = inputs.centre
    if centre is None:
        return (), ()
    solid: dict[Point, None] = {}
    dashed: dict[Point, None] = {}
    for found in departures:
        pattern = inputs.patterns.get(found.trip_id)
        if pattern is None:
            continue
        drawn = line_shape(
            departure=found, patterns=inputs.patterns, shapes=inputs.shapes,
        )
        matching = [
            line
            for line in lines
            if drawn is not None and line.shape_id == drawn.shape_id
        ]
        if not matching:
            continue
        spans = [line.solid for line in matching if line.solid is not None]
        for index, stop in enumerate(pattern.stops):
            if stop.point is None or stop.passes or not visible(
                at=screen_point(
                    centre=centre,
                    mpp=ZOOMS[inputs.zoom],
                    point=stop.point,
                    projection=inputs.projection,
                    width=inputs.width,
                ),
                width=inputs.width,
            ):
                continue
            along = None if pattern.along is None else pattern.along[index]
            inside = along is not None and any(
                start <= along <= end for start, end in spans
            )
            (solid if inside else dashed)[stop.point] = None
    return tuple(solid), tuple(point for point in dashed if point not in solid)


def plain_map_view(
    *, inputs: MapInputs, choice: LineChoice = LineChoice(),
) -> MapView:
    """
    Build a plain pin's Map view with its lines and rings.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    choice : LineChoice
        The kinds left out and the day's other lines.

    Returns
    -------
    MapView
        The view, with no route and the lines and rings of the drawn
        departures and the extras. Its markers are from every departure.
    """
    built = build_map_view(inputs=inputs)
    lines = plain_lines(inputs=inputs, choice=choice)
    solid, dashed = ring_points(
        inputs=inputs,
        lines=lines,
        departures=tuple(
            found
            for found in (*inputs.departures, *choice.extras)
            if found.facts.kind not in choice.hidden
        ),
    )
    return replace(
        built,
        base=replace(
            built.base,
            route=(),
            lines=lines,
            stops=solid,
            dashed_stops=dashed,
        ),
    )
