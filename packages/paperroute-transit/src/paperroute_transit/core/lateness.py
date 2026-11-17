"""Where one vehicle position lies on its trip, and how late that makes it."""

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from itertools import pairwise
from typing import Final

from paperroute_transit.core.geo import (
    Point,
    Shape,
    make_shape,
    project_onto_segment,
)
from paperroute_transit.core.model import service_seconds
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import VehiclePosition

OFF_LINE: Final[float] = 100.0  # metres
AT_STOP: Final[float] = 30.0  # metres
CANDIDATE_SLACK: Final[float] = 30.0  # metres
SAME_STRETCH: Final[float] = 150.0  # metres
BEARING_LIMIT: Final[float] = 100.0  # degrees
FULL_TURN: Final[float] = 360.0  # degrees


@dataclass(frozen=True)
class Line:
    """A trip's path and where its stops lie on it.

    Attributes
    ----------
    shape : Shape
        The path the trip follows.
    stops : tuple[float, ...]
        Metres along the shape at each stop.
    """

    shape: Shape
    stops: tuple[float, ...]


@dataclass(frozen=True)
class Stretch:
    """A place on the line a position could be.

    Attributes
    ----------
    along : float
        Metres along the line.
    gap : float
        Metres from the position to the line.
    bearing : float
        Compass degrees of the line's segment there.
    """

    along: float
    gap: float
    bearing: float


@dataclass(frozen=True)
class Placed:
    """A position placed on its trip.

    Attributes
    ----------
    along : float
        Metres along the line.
    late : int
        Seconds behind the timetable, negative when early.
    ends : float
        Metres to the nearer of the first and last stop.
    """

    along: float
    late: int
    ends: float


def line_of(*, pattern: TripPattern, shape: Shape | None) -> Line | None:
    """
    Give the line a trip runs along.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's shape, or None when it has none.

    Returns
    -------
    Line | None
        The shape with the stops measured on it, else straight hops
        between the stops; None with fewer than two stops or a stop that
        has no point.
    """
    points = [stop.point for stop in pattern.stops]
    placed_points = [point for point in points if point is not None]
    if len(points) < 2 or len(placed_points) < len(points):
        return None
    if shape is not None and pattern.along is not None:
        return Line(shape=shape, stops=pattern.along)
    straight = make_shape(shape_id=pattern.trip_id, points=placed_points)
    return Line(shape=straight, stops=straight.along)


def timetable_at(
    *, pattern: TripPattern, line: Line, along: float,
) -> float:
    """
    Read the timetable time at a place on the line.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    line : Line
        The trip's line.
    along : float
        Metres along the line.

    Returns
    -------
    float
        Seconds into the service day; the first departure before the
        first stop and the last arrival beyond the last.
    """
    stops, calls = line.stops, pattern.stops
    if along <= stops[0]:
        return calls[0].departs
    if along >= stops[-1]:
        return calls[-1].arrives
    index = bisect_right(stops, along) - 1
    span = stops[index + 1] - stops[index]
    leave = calls[index].departs
    if span == 0.0:
        return leave
    run = calls[index + 1].arrives - leave
    return leave + (along - stops[index]) / span * run


def stretches(*, line: Line, point: Point) -> tuple[Stretch, ...]:
    """
    Find the separate places on the line a point could be.

    Parameters
    ----------
    line : Line
        The trip's line.
    point : Point
        The position in metres.

    Returns
    -------
    tuple[Stretch, ...]
        The nearest segment of each stretch of the line close to the
        point, in order along the line. A line that doubles back gives
        more than one; a line of fewer than two points gives none.
    """
    shape = line.shape
    found = []
    for (start, end), base in zip(pairwise(shape.points), shape.along):
        gap, offset = project_onto_segment(point=point, start=start, end=end)
        heading = math.atan2(end[0] - start[0], end[1] - start[1])
        found.append(
            Stretch(
                along=base + offset,
                gap=gap,
                bearing=math.degrees(heading) % FULL_TURN,
            ),
        )
    if not found:
        return ()
    best = min(stretch.gap for stretch in found)
    near = [s for s in found if s.gap <= best + CANDIDATE_SLACK]
    return _grouped(near=near)


def _grouped(*, near: Sequence[Stretch]) -> tuple[Stretch, ...]:
    """
    Split segments in order along the line into stretches.

    Parameters
    ----------
    near : Sequence[Stretch]
        The close segments, in order along the line.

    Returns
    -------
    tuple[Stretch, ...]
        The nearest segment of each stretch.
    """
    groups: list[Stretch] = []
    last = -math.inf
    for stretch in near:
        if stretch.along - last > SAME_STRETCH or not groups:
            groups.append(stretch)
        elif stretch.gap < groups[-1].gap:
            groups[-1] = stretch
        last = stretch.along
    return tuple(groups)


def turn(*, one: float, two: float) -> float:
    """
    Find the smallest angle between two compass bearings.

    Parameters
    ----------
    one : float
        A bearing in degrees.
    two : float
        Another bearing in degrees.

    Returns
    -------
    float
        Degrees from 0 to 180.
    """
    apart = abs(one - two) % FULL_TURN
    return min(apart, FULL_TURN - apart)


def chosen(
    *,
    found: Sequence[Stretch],
    bearing: float | None,
    seconds: int,
    pattern: TripPattern,
    line: Line,
) -> Stretch:
    """
    Pick the stretch a vehicle is most likely on.

    Parameters
    ----------
    found : Sequence[Stretch]
        The candidate stretches.
    bearing : float | None
        The vehicle's heading in degrees.
    seconds : int
        Seconds into the service day.
    pattern : TripPattern
        The trip.
    line : Line
        The trip's line.

    Returns
    -------
    Stretch
        Of the stretches that run the way the vehicle heads, the one
        whose timetable time is nearest the time; every stretch is kept
        when none runs that way or there is no heading.
    """
    if bearing is not None:
        heading = [
            s for s in found
            if turn(one=s.bearing, two=bearing) <= BEARING_LIMIT
        ]
        found = heading or found
    return min(
        found,
        key=lambda s: abs(
            seconds - timetable_at(pattern=pattern, line=line, along=s.along),
        ),
    )


@dataclass(frozen=True)
class Arrived:
    """A position at the last stop of its trip.

    Attributes
    ----------
    late : int
        Seconds past the scheduled arrival there, negative when early.
    """

    late: int


def locate(
    *,
    pattern: TripPattern,
    line: Line,
    vehicle: VehiclePosition,
    day: date,
    zone: tzinfo,
) -> Placed | Arrived | None:
    """
    Place a vehicle on its trip and read how late it is.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    line : Line
        The trip's line.
    vehicle : VehiclePosition
        The reported position.
    day : date
        The service date.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    Placed | Arrived | None
        Arrived within 30 m of the last stop; None when the position is
        off the line or waiting at the first stop before its departure.
    """
    moment = datetime.fromtimestamp(vehicle.timestamp, tz=zone)
    seconds = service_seconds(day=day, moment=moment, zone=zone)
    near = stretches(line=line, point=vehicle.point)
    if not near:
        return None
    stretch = chosen(
        found=near,
        bearing=vehicle.bearing,
        seconds=seconds,
        pattern=pattern,
        line=line,
    )
    first, last = line.stops[0], line.stops[-1]
    waiting = (
        stretch.along <= first + AT_STOP
        and seconds < pattern.stops[0].departs
    )
    if stretch.gap > OFF_LINE or waiting:
        return None
    if stretch.along >= last - AT_STOP:
        return Arrived(late=round(seconds - pattern.stops[-1].arrives))
    due = timetable_at(pattern=pattern, line=line, along=stretch.along)
    return Placed(
        along=stretch.along,
        late=round(seconds - due),
        ends=min(stretch.along - first, last - stretch.along),
    )


def placed(
    *,
    pattern: TripPattern,
    line: Line,
    vehicle: VehiclePosition,
    day: date,
    zone: tzinfo,
) -> Placed | None:
    """
    Place a vehicle on its trip and read how late it is.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    line : Line
        The trip's line.
    vehicle : VehiclePosition
        The reported position.
    day : date
        The service date.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    Placed | None
        None when the position is off the line, waiting at the first
        stop before its departure, or at the last stop.
    """
    found = locate(
        pattern=pattern, line=line, vehicle=vehicle, day=day, zone=zone,
    )
    return found if isinstance(found, Placed) else None
