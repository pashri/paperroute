"""Buses of the hero's route, placed along the hero's line."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from itertools import pairwise
from typing import Final

from paperroute_transit.core.geo import Point, project_onto_segment
from paperroute_transit.core.memo import SameMemo
from paperroute_transit.core.model import service_seconds
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.route_trips import RouteTrip

MEMO_SIZE: Final[int] = 512
HERO_STOPS: Final[SameMemo[tuple[tuple[int, float], ...]]] = SameMemo(
    size=MEMO_SIZE,
)


@dataclass(frozen=True)
class GaugeBus:
    """
    One bus on the hero's line.

    Attributes
    ----------
    spot : float
        Fractional index into the hero pattern's stops.
    right : bool
        True when the bus runs the hero's way.
    live : bool
        True for a reported vehicle, False for a timetabled trip.
    trip_id : str
        Trip ID.
    """

    spot: float
    right: bool
    live: bool
    trip_id: str


def mapped(*, pattern: TripPattern) -> list[tuple[int, Point]]:
    """
    List the stops of a pattern that have a position.

    Parameters
    ----------
    pattern : TripPattern
        The trip.

    Returns
    -------
    list[tuple[int, Point]]
        Each such stop's index and position, in running order.
    """
    return [
        (index, stop.point)
        for index, stop in enumerate(pattern.stops)
        if stop.point is not None
    ]


def same_way(*, hero: TripPattern, pattern: TripPattern) -> bool | None:
    """
    Tell whether a trip runs the hero's way.

    Direction ids decide when both trips have one and share a route ID.
    Otherwise the order of the first two stations the trips share
    decides. With fewer than two shared stations the way is unknown.

    Parameters
    ----------
    hero : TripPattern
        The hero's trip.
    pattern : TripPattern
        The other trip.

    Returns
    -------
    bool | None
        True when it runs the same way, False when it runs the other
        way, None when the trips share fewer than two stations.
    """
    if (
        hero.direction is not None
        and pattern.direction is not None
        and hero.route_id == pattern.route_id
    ):
        return hero.direction == pattern.direction
    order: dict[str, int] = {}
    for index, stop in enumerate(hero.stops):
        order.setdefault(stop.station, index)
    shared = [
        order[stop.station]
        for stop in pattern.stops
        if stop.station in order
    ]
    if len(shared) < 2:
        return None
    return shared[0] < shared[1]


def along_index(*, pattern: TripPattern, point: Point) -> float:
    """
    Place a point on a trip's stops.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    point : Point
        Position in metres.

    Returns
    -------
    float
        Index of the nearest position on the segments between
        consecutive stops that have positions; a fraction between two
        stops. Zero when fewer than two stops have positions.
    """
    best = (math.inf, 0.0)
    for (first, start), (second, end) in pairwise(mapped(pattern=pattern)):
        gap, offset = project_onto_segment(
            point=point, start=start, end=end,
        )
        length = math.dist(start, end)
        share = offset / length if length else 0.0
        if gap < best[0]:
            best = (gap, first + share * (second - first))
    return best[1]


def timetable_index(*, pattern: TripPattern, seconds: int) -> float | None:
    """
    Place a moment on a trip's stops by its timetable.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    seconds : int
        Seconds into the trip's service day.

    Returns
    -------
    float | None
        The stop's index while the trip dwells there, a fraction
        between stops in proportion to time, or None before the first
        departure and after the last arrival.
    """
    stops = pattern.stops
    if not stops or seconds < stops[0].departs or seconds > stops[-1].arrives:
        return None
    for index, (here, there) in enumerate(pairwise(stops)):
        if seconds <= here.departs:
            return float(index)
        if seconds < there.arrives:
            span = there.arrives - here.departs
            return index + (seconds - here.departs) / span
    return float(len(stops) - 1)


@dataclass(frozen=True)
class HeroIndex:
    """
    The hero's stops that have a position.

    Attributes
    ----------
    order : dict[str, int]
        Index of the first such stop at each station.
    stops : list[tuple[int, Point]]
        Each such stop's index and position, in running order.
    """

    order: dict[str, int]
    stops: list[tuple[int, Point]]


def index_hero(*, hero: TripPattern) -> HeroIndex:
    """
    Index the hero's stops that have a position.

    Parameters
    ----------
    hero : TripPattern
        The hero's trip.

    Returns
    -------
    HeroIndex
        The index.
    """
    stops = mapped(pattern=hero)
    order: dict[str, int] = {}
    for index, _ in stops:
        order.setdefault(hero.stops[index].station, index)
    return HeroIndex(order=order, stops=stops)


def nearest_stop(*, known: HeroIndex, point: Point) -> int:
    """
    Find the hero's stop nearest a point.

    Parameters
    ----------
    known : HeroIndex
        The hero's stops that have a position.
    point : Point
        Position in metres.

    Returns
    -------
    int
        Index of the nearest stop that has a position; zero when none
        has.
    """
    if not known.stops:
        return 0
    return min(
        known.stops,
        key=lambda stop: math.dist(stop[1], point),
    )[0]


def hero_stops(
    *, known: HeroIndex, pattern: TripPattern, right: bool
) -> tuple[tuple[int, float], ...]:
    """
    Map a trip's stops onto the hero's stops.

    Parameters
    ----------
    known : HeroIndex
        The hero's stops that have a position.
    pattern : TripPattern
        The other trip.
    right : bool
        True when it runs the hero's way.

    Returns
    -------
    tuple[tuple[int, float], ...]
        For each of its stops with a position, its index and the
        hero's stop it maps to. A same-way stop at a station the hero
        calls at maps to it; any other stop maps to the nearest.
    """
    result: list[tuple[int, float]] = []
    for index, point in mapped(pattern=pattern):
        station = pattern.stops[index].station
        if right and station in known.order:
            result.append((index, float(known.order[station])))
        else:
            result.append(
                (index, float(nearest_stop(known=known, point=point)))
            )
    return tuple(result)


def hero_spot(
    *,
    hero: TripPattern,
    pattern: TripPattern,
    index: float,
    right: bool,
    known: HeroIndex | None = None,
) -> float:
    """
    Turn a trip's own stop index into a spot on the hero's stops.

    Parameters
    ----------
    hero : TripPattern
        The hero's trip.
    pattern : TripPattern
        The other trip.
    index : float
        Fractional index into the other trip's stops.
    right : bool
        True when it runs the hero's way.
    known : HeroIndex | None
        The hero's index when already made.

    Returns
    -------
    float
        The spot, interpolated between the hero stops its two
        neighbouring stops map to; zero when the hero has no stop with a
        position.
    """
    inputs = (hero, pattern, right)
    stops = HERO_STOPS.get(inputs=inputs)
    if stops is None:
        stops = HERO_STOPS.put(
            inputs=inputs,
            value=hero_stops(
                known=known or index_hero(hero=hero),
                pattern=pattern,
                right=right,
            ),
        )
    if not stops:
        return 0.0
    if index <= stops[0][0]:
        return stops[0][1]
    for (first, low), (second, high) in pairwise(stops):
        if index <= second:
            return low + (high - low) * (index - first) / (second - first)
    return stops[-1][1]


def last_index(*, pattern: TripPattern) -> int:
    """
    Find the last stop of a trip that has a position.

    Parameters
    ----------
    pattern : TripPattern
        The trip.

    Returns
    -------
    int
        Its index; zero when no stop has a position.
    """
    stops = mapped(pattern=pattern)
    return stops[-1][0] if stops else 0


def live_bus(
    *,
    hero: TripPattern,
    pattern: TripPattern,
    vehicle: VehiclePosition,
    known: HeroIndex,
) -> GaugeBus | None:
    """
    Place a reported vehicle on the hero's line.

    Parameters
    ----------
    hero : TripPattern
        The hero's trip.
    pattern : TripPattern
        The vehicle's trip.
    vehicle : VehiclePosition
        The report.
    known : HeroIndex
        The hero's stops that have a position.

    Returns
    -------
    GaugeBus | None
        The bus, or None once it has reached its last stop or when its
        way is unknown.
    """
    index = along_index(pattern=pattern, point=vehicle.point)
    if index >= last_index(pattern=pattern):
        return None
    right = same_way(hero=hero, pattern=pattern)
    if right is None:
        return None
    spot = hero_spot(
        hero=hero, pattern=pattern, index=index, right=right, known=known,
    )
    return GaugeBus(
        spot=spot, right=right, live=True, trip_id=vehicle.trip_id,
    )


def timetable_bus(
    *,
    hero: TripPattern,
    pattern: TripPattern,
    trip: RouteTrip,
    now: datetime,
    zone: tzinfo,
    known: HeroIndex,
) -> GaugeBus | None:
    """
    Place a timetabled trip on the hero's line.

    Parameters
    ----------
    hero : TripPattern
        The hero's trip.
    pattern : TripPattern
        The trip's pattern.
    trip : RouteTrip
        The trip.
    now : datetime
        The moment.
    zone : tzinfo
        Local time zone of the service day.
    known : HeroIndex
        The hero's stops that have a position.

    Returns
    -------
    GaugeBus | None
        The bus, or None when the trip is not under way, has reached
        its last stop or runs a way that is unknown.
    """
    seconds = service_seconds(day=trip.day, moment=now, zone=zone)
    index = timetable_index(pattern=pattern, seconds=seconds)
    if index is None or index >= len(pattern.stops) - 1:
        return None
    right = same_way(hero=hero, pattern=pattern)
    if right is None:
        return None
    spot = hero_spot(
        hero=hero, pattern=pattern, index=index, right=right, known=known,
    )
    return GaugeBus(
        spot=spot, right=right, live=False, trip_id=trip.trip_id,
    )


def gauge_buses(
    *,
    hero: TripPattern,
    patterns: Mapping[str, TripPattern | None],
    vehicles: Sequence[VehiclePosition],
    trips_running: Sequence[RouteTrip],
    now: datetime,
    zone: tzinfo,
) -> tuple[GaugeBus, ...]:
    """
    Place every bus of the hero's route on the hero's line.

    Parameters
    ----------
    hero : TripPattern
        The hero's trip.
    patterns : Mapping[str, TripPattern | None]
        Patterns by trip ID; a vehicle or trip without one is skipped.
    vehicles : Sequence[VehiclePosition]
        Reported vehicles on the route.
    trips_running : Sequence[RouteTrip]
        The route's trips under way by the timetable. A trip that has a
        reported vehicle is drawn from the report only.
    now : datetime
        The moment.
    zone : tzinfo
        Local time zone of the service days.

    Returns
    -------
    tuple[GaugeBus, ...]
        The buses, by spot.
    """
    known = index_hero(hero=hero)
    buses: list[GaugeBus | None] = [
        live_bus(
            hero=hero, pattern=pattern, vehicle=vehicle, known=known,
        )
        for vehicle in vehicles
        if (pattern := patterns.get(vehicle.trip_id)) is not None
    ]
    reported = {vehicle.trip_id for vehicle in vehicles}
    buses.extend(
        timetable_bus(
            hero=hero,
            pattern=pattern,
            trip=trip,
            now=now,
            zone=zone,
            known=known,
        )
        for trip in trips_running
        if trip.trip_id not in reported
        and (pattern := patterns.get(trip.trip_id)) is not None
    )
    return tuple(
        sorted(
            (bus for bus in buses if bus is not None),
            key=lambda bus: bus.spot,
        )
    )
