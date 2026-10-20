"""Where a strip's vehicles are drawn."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from paperroute.core.map_view import (
    Marker,
    is_fresh,
    position_age,
    timetable_moment,
)
from paperroute.core.model import Departure, service_seconds
from paperroute.core.patterns import TripPattern, hop_at, hop_near
from paperroute.core.positions import VehiclePosition
from paperroute.core.strip_badges import Badge, Keepouts, Spot, pushed
from paperroute.core.strip_model import ROW_H
from paperroute.core.strip_times import TripInputs, window_departures

type Heard = Mapping[str, tuple[VehiclePosition, float]]


@dataclass(frozen=True)
class Leg:
    """
    One trip a strip departure's vehicle runs, with its vehicle if heard.

    Attributes
    ----------
    pattern : TripPattern
        The trip's pattern.
    found : tuple[VehiclePosition, float] | None
        Its vehicle and the seconds since it reported, or None.
    """

    pattern: TripPattern
    found: tuple[VehiclePosition, float] | None


def heard_trips(
    *, trips: TripInputs,
) -> dict[str, tuple[VehiclePosition, float]]:
    """
    Find the most recently heard vehicle of each trip.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.

    Returns
    -------
    dict[str, tuple[VehiclePosition, float]]
        Each trip's vehicle and the seconds since it reported.
    """
    best: dict[str, tuple[VehiclePosition, float]] = {}
    for snapshot in trips.positions.values():
        for vehicle in snapshot.vehicles:
            age = position_age(
                vehicle=vehicle, snapshot=snapshot, now=trips.now,
            )
            known = best.get(vehicle.trip_id)
            if known is None or age < known[1]:
                best[vehicle.trip_id] = (vehicle, age)
    return best


def vehicle_of(*, departure: Departure, heard: Heard
               ) -> tuple[VehiclePosition, float] | None:
    """
    Find a departure's vehicle by its trip ID or an alias.

    Parameters
    ----------
    departure : Departure
        Departure.
    heard : Heard
        Each trip's vehicle and age.

    Returns
    -------
    tuple[VehiclePosition, float] | None
        The freshest match, or None.
    """
    found = [
        heard[trip]
        for trip in (departure.trip_id, *departure.aliases)
        if trip in heard
    ]
    return min(found, key=lambda each: each[1], default=None)


def legs_of(
    *, departure: Departure, trips: TripInputs, heard: Heard,
) -> tuple[Leg, ...]:
    """
    List the trips a departure's vehicle runs whose patterns are loaded.

    Parameters
    ----------
    departure : Departure
        The trip's departure from the From.
    trips : TripInputs
        The strip's departures and live data.
    heard : Heard
        Each trip's vehicle and age.

    Returns
    -------
    tuple[Leg, ...]
        The departure's own trip, its vehicle matched by trip ID or
        alias, then the trip it carries on as when that pattern is
        loaded, its vehicle matched by that trip ID; none without a
        pattern for the own trip.
    """
    own = trips.patterns.get(departure.trip_id)
    if own is None:
        return ()
    first = Leg(
        pattern=own, found=vehicle_of(departure=departure, heard=heard),
    )
    then = departure.facts.then
    after = None if then is None else trips.patterns.get(then.trip_id)
    if then is None or after is None:
        return (first,)
    return first, Leg(pattern=after, found=heard.get(then.trip_id))


def fresh_leg(*, legs: Sequence[Leg]) -> Leg | None:
    """
    Find the leg with the freshest fresh position.

    Parameters
    ----------
    legs : Sequence[Leg]
        The departure's legs.

    Returns
    -------
    Leg | None
        That leg, or None when no leg has a fresh position.
    """
    fresh = [
        leg
        for leg in legs
        if leg.found is not None and is_fresh(age=leg.found[1])
    ]
    return min(
        fresh,
        key=lambda leg: math.inf if leg.found is None else leg.found[1],
        default=None,
    )


def current_leg(
    *, legs: Sequence[Leg], departure: Departure, now: datetime,
) -> tuple[Leg, tuple[int, float]] | None:
    """
    Find the leg a departure's vehicle is on now, and its hop.

    Parameters
    ----------
    legs : Sequence[Leg]
        The departure's legs, from `legs_of`.
    departure : Departure
        The departure.
    now : datetime
        Current time.

    Returns
    -------
    tuple[Leg, tuple[int, float]] | None
        A fresh position's leg and nearest hop. Otherwise the own leg's
        timetable hop until its last arrival; after it, the next leg's
        timetable hop, or (0, 0.0) while that leg hasn't left its first
        stop. None with no legs or no hop.
    """
    if not legs:
        return None
    if (fresh := fresh_leg(legs=legs)) is not None and fresh.found:
        hop = hop_near(pattern=fresh.pattern, point=fresh.found[0].point)
        return None if hop is None else (fresh, hop)
    seconds = service_seconds(
        day=departure.service_day, moment=timetable_moment(now=now),
    )
    own = legs[0]
    stops = own.pattern.stops
    if len(legs) == 1 or not stops or seconds <= stops[-1].arrives:
        hop = hop_at(pattern=own.pattern, seconds=seconds)
        return None if hop is None else (own, hop)
    after = legs[1]
    if after.pattern.stops and seconds < after.pattern.stops[0].departs:
        return after, (0, 0.0)
    hop = hop_at(pattern=after.pattern, seconds=seconds)
    return None if hop is None else (after, hop)


def trip_marker(
    *,
    departure: Departure,
    trips: TripInputs,
    spots: Mapping[str, Spot],
    heard: Heard,
) -> Badge | None:
    """
    Place one trip's badge on the strip.

    Parameters
    ----------
    departure : Departure
        The trip's departure from the From.
    trips : TripInputs
        The strip's departures and live data.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.
    heard : Heard
        Each trip's vehicle and age.

    Returns
    -------
    Badge | None
        A solid badge at a fresh position's nearest hop, else a hollow one
        at the timetable hop, on the trip or the one it carries on as and
        labelled with that trip's route; None without a pattern, a hop,
        or both of the hop's rows.
    """
    legs = legs_of(departure=departure, trips=trips, heard=heard)
    current = current_leg(legs=legs, departure=departure, now=trips.now)
    if current is None:
        return None
    leg, (index, fraction) = current
    ages = [each.found[1] for each in legs if each.found is not None]
    live = bool(ages) and is_fresh(age=min(ages))
    return hop_marker(
        route=leg.pattern.route,
        ends=tuple(
            spots.get(trips.station_of.get(stop.station, stop.station))
            for stop in leg.pattern.stops[index:index + 2]
        ),
        fraction=fraction,
        age=None if live or not ages else min(ages) // 60 * 60,
        live=live,
    )


def hop_marker(
    *,
    route: str,
    ends: tuple[Spot | None, ...],
    fraction: float,
    age: float | None,
    live: bool,
) -> Badge | None:
    """
    Make a badge part way along a hop between two strip rows.

    Parameters
    ----------
    route : str
        Route label.
    ends : tuple[Spot | None, ...]
        The spots of the hop's two stops, each None when not a visible
        row.
    fraction : float
        Share of the hop run.
    age : float | None
        Seconds since a stale vehicle reported, or None.
    live : bool
        True for a fresh position.

    Returns
    -------
    Badge | None
        The badge on the later row's track, with a wedge down the strip
        when that row is lower and up otherwise, if live; None when either
        end of the hop is not a visible row. Bound for an exit row, it
        rides the earlier row's track until within a row of the exit,
        then sits on the exit's stub end as a spur badge.
    """
    start, end = ends
    if start is None or end is None:
        return None
    y = start.y + (end.y - start.y) * fraction
    spur = end.exit and abs(end.y - y) < ROW_H
    return Badge(
        marker=Marker(
            route=route,
            x=start.x if end.exit and not spur else end.x,
            y=y,
            bearing=(180.0 if end.y > start.y else 0.0) if live else None,
            live=live,
            pinned=True,
            age=age,
        ),
        low=min(start.y, end.y),
        high=max(start.y, end.y),
        spur=spur,
    )


def without_shown_next(
    *, departures: Sequence[Departure],
) -> list[Departure]:
    """
    Drop carry-ons onto trips that have their own strip departure.

    Parameters
    ----------
    departures : Sequence[Departure]
        The strip's departures in the window.

    Returns
    -------
    list[Departure]
        The departures, with no carry-on where the next trip is one of
        them, so its vehicle gets one badge.
    """
    shown = {departure.trip_id for departure in departures}
    return [
        (
            replace(departure, facts=replace(departure.facts, then=None))
            if departure.facts.then is not None
            and departure.facts.then.trip_id in shown
            else departure
        )
        for departure in departures
    ]


def vehicle_markers(*, trips: TripInputs, keep: Keepouts
                    ) -> tuple[Marker, ...]:
    """
    Place the strip's vehicles on its visible rows.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    keep : Keepouts
        The visible rows' spots, the discs and the top.

    Returns
    -------
    tuple[Marker, ...]
        One badge per strip trip in the window, not cancelled, that can
        be placed, each on its own hop, kept off the names, the discs and
        each other (see `pushed`). A trip that carries on as another
        strip trip in the window ends at the change, so the next trip's
        vehicle has one badge.
    """
    key = trips.key
    spots = keep.spots
    if key is None or not spots:
        return ()
    heard = heard_trips(trips=trips)
    departures = without_shown_next(departures=window_departures(
        departures=[
            item.departure for item in trips.departures if not item.cancelled
        ],
        key=key,
        now=trips.now,
    ))
    placed = (
        trip_marker(
            departure=departure, trips=trips, spots=spots, heard=heard,
        )
        for departure in departures
    )
    return tuple(
        each.marker
        for each in pushed(
            badges=[each for each in placed if each is not None], keep=keep,
        )
    )
