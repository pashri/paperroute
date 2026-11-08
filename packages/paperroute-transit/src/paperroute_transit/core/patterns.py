"""A trip's stops and running dates, for placing vehicles on the map."""

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, tzinfo
from itertools import pairwise

from paperroute_transit.core.geo import (
    Point,
    Shape,
    point_along,
    project_onto_segment,
    stop_distances,
)
from paperroute_transit.core.model import Mode, service_seconds


@dataclass(frozen=True)
class PatternStop:
    """One call of a trip.

    Attributes
    ----------
    arrives : int
        Arrival in seconds from the start of the service day.
    departs : int
        Departure in seconds from the start of the service day.
    point : Point | None
        Position in metres, or None when the stop has no coordinates.
    station : str
        The stop's parent station, or the stop itself when it has none.
    name : str
        That station's name; empty when not known.
    passes : bool
        True when the trip runs through without picking up or setting
        down.
    """

    arrives: int
    departs: int
    point: Point | None
    station: str = ''
    name: str = ''
    passes: bool = False


@dataclass(frozen=True)
class TripPattern:
    """A trip's route, shape, calls and running dates.

    Attributes
    ----------
    trip_id : str
        The requested trip ID, which may be an alias.
    route : str
        Route short name.
    mode : Mode
        Kind of service.
    shape_id : str | None
        Namespaced shape ID, or None when the trip has no shape.
    stops : tuple[PatternStop, ...]
        Calls in running order.
    days : tuple[date, ...]
        Service dates the trip runs on, near the date asked about.
    along : tuple[float, ...] | None
        Metres along the shape at each stop, once worked out.
    next_trip : str | None
        The trip the vehicle runs on through as, keeping its passengers;
        None when it doesn't or it isn't known.
    """

    trip_id: str
    route: str
    mode: Mode
    shape_id: str | None
    stops: tuple[PatternStop, ...]
    days: tuple[date, ...]
    along: tuple[float, ...] | None = None
    next_trip: str | None = None


def measure(*, pattern: TripPattern, shape: Shape | None) -> TripPattern:
    """
    Place a trip's stops along its shape.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's shape, or None when it has none.

    Returns
    -------
    TripPattern
        The trip with `along` set, or unchanged when there is no shape or
        a stop has no point.
    """
    points = [stop.point for stop in pattern.stops]
    if shape is None or None in points:
        return pattern
    return replace(
        pattern,
        along=stop_distances(
            shape=shape,
            stops=[point for point in points if point is not None],
        ),
    )


def between(
    *,
    pattern: TripPattern,
    shape: Shape | None,
    index: int,
    fraction: float,
) -> Point | None:
    """
    Find the point a fraction of the way from one stop to the next.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's shape.
    index : int
        The stop the trip last left.
    fraction : float
        Share of the hop already run, from 0 to 1.

    Returns
    -------
    Point | None
        A point along the shape when the stops are measured on it, else on
        the straight line between the stops, or None when either stop has
        no point.
    """
    if shape is not None and pattern.along is not None:
        start, end = pattern.along[index : index + 2]
        return point_along(
            shape=shape,
            distance=start + (end - start) * fraction,
        )
    first, second = pattern.stops[index].point, pattern.stops[index + 1].point
    if first is None or second is None:
        return None
    return (
        first[0] + (second[0] - first[0]) * fraction,
        first[1] + (second[1] - first[1]) * fraction,
    )


def hop_at(
    *, pattern: TripPattern, seconds: int,
) -> tuple[int, float] | None:
    """
    Find the hop a trip is on at a time on its service day.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    seconds : int
        Seconds into the service day.

    Returns
    -------
    tuple[int, float] | None
        The index of the stop last left and the share of the hop run,
        from 0 to 1: 0 while dwelling at that stop, and 1 on the last hop
        once the trip has arrived. None before the first arrival, after
        the last, or for a trip with fewer than two stops.
    """
    stops = pattern.stops
    running = len(stops) > 1 and (
        stops[0].arrives <= seconds <= stops[-1].arrives
    )
    if not running:
        return None
    for index, stop in enumerate(stops[:-1]):
        if stop.arrives <= seconds <= stop.departs:
            return index, 0.0
        after = stops[index + 1]
        if seconds < after.arrives:
            run = (seconds - stop.departs) / (after.arrives - stop.departs)
            return index, run
    return len(stops) - 2, 1.0


def hop_near(
    *, pattern: TripPattern, point: Point,
) -> tuple[int, float] | None:
    """
    Find the straight hop between a trip's stops nearest a point.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    point : Point
        A position in metres.

    Returns
    -------
    tuple[int, float] | None
        The index of the hop's first stop and the share of the hop at the
        point's projection onto it; the earliest of equally near hops.
        None when no hop has points at both ends.
    """
    hops = [
        (
            project_onto_segment(point=point, start=start, end=end),
            index,
            math.dist(start, end),
        )
        for index, (start, end) in enumerate(
            pairwise(stop.point for stop in pattern.stops),
        )
        if start is not None and end is not None
    ]
    if not hops:
        return None
    (_, along), index, length = min(hops, key=lambda hop: hop[0][0])
    return index, along / length if length else 0.0


def point_at(
    *,
    pattern: TripPattern,
    shape: Shape | None,
    seconds: int,
) -> Point | None:
    """
    Find where a running trip is at a time on its service day.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's shape.
    seconds : int
        Seconds into the service day, between the first departure and the
        last arrival.

    Returns
    -------
    Point | None
        The timetable position, or None when the stops lack points.
    """
    hop = hop_at(pattern=pattern, seconds=seconds)
    if hop is None:
        return pattern.stops[0].point if len(pattern.stops) == 1 else None
    index, fraction = hop
    if fraction in (0.0, 1.0):
        return pattern.stops[index + int(fraction)].point
    return between(
        pattern=pattern, shape=shape, index=index, fraction=fraction,
    )


def timetable_point(
    *,
    pattern: TripPattern,
    shape: Shape | None,
    now: datetime,
    zone: tzinfo,
) -> Point | None:
    """
    Find where the timetable puts a trip now.

    The first running date in `pattern.days` wins, so a trip timed past
    24:00 is found on the previous service day.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's shape, or None when it has none.
    now : datetime
        Aware time.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    Point | None
        The position, or None when the trip isn't running or its stops
        lack points.
    """
    if not pattern.stops:
        return None
    for day in pattern.days:
        seconds = service_seconds(day=day, moment=now, zone=zone)
        if pattern.stops[0].departs <= seconds <= pattern.stops[-1].arrives:
            return point_at(pattern=pattern, shape=shape, seconds=seconds)
    return None
