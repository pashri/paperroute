"""Where a strip's vehicles are drawn."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo

from paperroute_transit.core.map_vehicles import position_age
from paperroute_transit.core.map_view import Marker, is_fresh
from paperroute_transit.core.model import Departure, service_seconds
from paperroute_transit.core.patterns import TripPattern, hop_at, hop_near
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.strip_approach import (
    REACHED,
    Approach,
    approach_calls,
    facing_spot,
    stop_reached,
    toward_from,
)
from paperroute_transit.core.strip_badges import Badge, Keepouts, Spot, pushed
from paperroute_transit.core.strip_hooks import hook_badge
from paperroute_transit.core.strip_model import ROW_H
from paperroute_transit.core.strip_times import TripInputs, window_departures
from paperroute_transit.core.strip_ways import (
    Way,
    Ways,
    call_rows,
    hop_ends,
    hop_way,
    way_badge,
)

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


def fresh_leg(*, legs: Sequence[Leg], limit: timedelta) -> Leg | None:
    """
    Find the leg with the freshest fresh position.

    Parameters
    ----------
    legs : Sequence[Leg]
        The departure's legs.
    limit : timedelta
        How old a position may be.

    Returns
    -------
    Leg | None
        That leg, or None when no leg has a fresh position.
    """
    fresh = [
        leg
        for leg in legs
        if leg.found is not None and is_fresh(age=leg.found[1], limit=limit)
    ]
    return min(
        fresh,
        key=lambda leg: math.inf if leg.found is None else leg.found[1],
        default=None,
    )


def current_leg(
    *,
    legs: Sequence[Leg],
    departure: Departure,
    now: datetime,
    limit: timedelta,
    zone: tzinfo,
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
        The frame time.
    limit : timedelta
        How old a position may be.
    zone : tzinfo
        Local time zone the times show in.

    Returns
    -------
    tuple[Leg, tuple[int, float]] | None
        A fresh position's leg and nearest hop. Otherwise the own leg's
        timetable hop until its last arrival; after it, the next leg's
        timetable hop, or (0, 0.0) while that leg hasn't left its first
        stop. None with no legs or no hop, which includes a next leg of
        fewer than two stops.
    """
    if not legs:
        return None
    fresh = fresh_leg(legs=legs, limit=limit)
    if fresh is not None and fresh.found:
        hop = hop_near(pattern=fresh.pattern, point=fresh.found[0].point)
        return None if hop is None else (fresh, hop)
    seconds = service_seconds(
        day=departure.service_day, moment=now, zone=zone,
    )
    return timetable_leg(legs=legs, seconds=seconds)


def timetable_leg(
    *, legs: Sequence[Leg], seconds: int,
) -> tuple[Leg, tuple[int, float]] | None:
    """
    Find the leg and hop the timetable puts a vehicle on.

    Parameters
    ----------
    legs : Sequence[Leg]
        The departure's legs, at least one.
    seconds : int
        Service-day seconds now.

    Returns
    -------
    tuple[Leg, tuple[int, float]] | None
        The own leg's hop until its last arrival; after it, the next
        leg's hop, or (0, 0.0) while that leg hasn't left its first
        stop. None with no hop.
    """
    own = legs[0]
    stops = own.pattern.stops
    if len(legs) == 1 or not stops or seconds <= stops[-1].arrives:
        hop = hop_at(pattern=own.pattern, seconds=seconds)
        return None if hop is None else (own, hop)
    after = legs[1]
    calls = after.pattern.stops
    if len(calls) > 1 and seconds < calls[0].departs:
        return after, (0, 0.0)
    hop = hop_at(pattern=after.pattern, seconds=seconds)
    return None if hop is None else (after, hop)


def hop_spots(
    *,
    pattern: TripPattern,
    index: int,
    trips: TripInputs,
    spots: Mapping[str, Spot],
    ways: Ways | None = None,
    found: Sequence[int | None] | None = None,
) -> tuple[Spot | None, ...]:
    """
    Find the spots of a hop's two stops.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    index : int
        The index of the hop's first stop.
    trips : TripInputs
        The strip's departures and live data.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.
    ways : Ways | None
        The visible rows in order, or None to look each station up in
        `spots`.
    found : Sequence[int | None] | None
        The trip's `call_rows` with `ways`, or None to work them out.

    Returns
    -------
    tuple[Spot | None, ...]
        Each stop's spot, None when not a visible row; one or none past
        the trip's last stop. With `ways`, a station called at twice
        takes its rows in order (see `hop_ends`).
    """
    if ways is not None:
        return hop_ends(
            pattern=pattern, index=index, station_of=trips.station_of,
            ways=ways, found=found,
        )
    return tuple(
        spots.get(trips.station_of.get(stop.station, stop.station))
        for stop in pattern.stops[index:index + 2]
    )


def approach_marker(
    *,
    departure: Departure,
    pattern: TripPattern,
    hop: tuple[int, float],
    trips: TripInputs,
    spots: Mapping[str, Spot],
    approach: Approach,
    look: Marker,
    ways: Ways | None = None,
) -> Badge | None:
    """
    Place a bus approaching a both-sides From.

    Parameters
    ----------
    departure : Departure
        The bus's departure from the From.
    pattern : TripPattern
        Its trip.
    hop : tuple[int, float]
        The index of its hop's first stop and the share of it run.
    trips : TripInputs
        The strip's departures and live data.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.
    approach : Approach
        The rows either side of the From.
    look : Marker
        The badge's route, age and liveness.
    ways : Ways | None
        The visible rows, rings and spurs, or None.

    Returns
    -------
    Badge | None
        A badge on the hop on from the stop it is at or last left when
        both of that hop's rows are visible, as for a bus standing at the
        From; else a badge on its road's hook (see `hook_badge`); else a
        facing badge on the track between the rows facing
        its calls by its progress (see `facing_spot`), its wedge towards
        the From; else None.
    """
    at = stop_reached(hop=hop)
    badge = hop_marker(
        route=look.route,
        ends=hop_spots(
            pattern=pattern, index=at, trips=trips, spots=spots, ways=ways,
        ),
        fraction=0.0,
        age=look.age,
        live=look.live,
    )
    calls = approach_calls(
        departure=departure, pattern=pattern, hop=hop, trips=trips,
        approach=approach,
    )
    if badge is None and calls is not None:
        badge = hook_badge(
            pattern=pattern, calls=calls, station_of=trips.station_of,
            approach=approach, spots=spots, look=look,
        )
    if badge is not None:
        return badge
    spot = facing_spot(
        departure=departure, pattern=pattern, hop=hop, trips=trips,
        approach=approach, spots=spots,
    )
    if spot is None:
        return None
    return Badge(
        marker=replace(
            look, x=spot.x, y=spot.y,
            bearing=toward_from(departure=departure),
        ),
        low=spot.y,
        high=spot.y,
        facing=True,
    )


def leg_look(*, legs: Sequence[Leg], leg: Leg, trips: TripInputs) -> Marker:
    """
    Make the unplaced marker for a trip's current leg.

    Parameters
    ----------
    legs : Sequence[Leg]
        The trip's legs.
    leg : Leg
        The current leg.
    trips : TripInputs
        The strip's departures and live data.

    Returns
    -------
    Marker
        A marker for the leg's route at the origin, live when any leg's
        position is fresh, else aged to the minute.
    """
    ages = [each.found[1] for each in legs if each.found is not None]
    live = bool(ages) and is_fresh(age=min(ages), limit=trips.stale_after)
    return Marker(
        route=leg.pattern.route,
        x=0.0,
        y=0.0,
        bearing=None,
        live=live,
        pinned=True,
        age=None if live or not ages else min(ages) // 60 * 60,
    )


def trip_marker(
    *,
    departure: Departure,
    trips: TripInputs,
    spots: Mapping[str, Spot],
    heard: Heard,
    approach: Approach | None = None,
    ways: Ways | None = None,
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
    approach : Approach | None
        The rows either side of a both-sides From, or None.
    ways : Ways | None
        The visible rows, rings and spurs, or None.

    Returns
    -------
    Badge | None
        A solid badge at a fresh position's nearest hop, else a hollow one
        at the timetable hop, on the trip or the one it carries on as and
        labelled with that trip's route, with no wedge once `REACHED` of
        its trip's last hop is run; on a ring or spur it rides that
        path (see `hop_way`). On a both-sides strip, a bus on its
        own trip whose hop has no badge is placed by `approach_marker`.
        None without a pattern, a hop, or a place to draw it.
    """
    legs = legs_of(departure=departure, trips=trips, heard=heard)
    current = current_leg(
        legs=legs,
        departure=departure,
        now=trips.now,
        limit=trips.stale_after,
        zone=trips.zone,
    )
    if current is None:
        return None
    leg, (index, fraction) = current
    look = leg_look(legs=legs, leg=leg, trips=trips)
    found = None if ways is None else call_rows(
        pattern=leg.pattern, station_of=trips.station_of, ways=ways,
    )
    badge = hop_marker(
        route=look.route,
        ends=hop_spots(
            pattern=leg.pattern, index=index, trips=trips, spots=spots,
            ways=ways, found=found,
        ),
        fraction=fraction,
        age=look.age,
        live=look.live,
        known=not (
            index == len(leg.pattern.stops) - 2 and fraction >= REACHED
        ),
        way=None if ways is None else hop_way(
            pattern=leg.pattern, index=index, station_of=trips.station_of,
            ways=ways, found=found,
        ),
    )
    if badge is not None or approach is None or leg is not legs[0]:
        return badge
    return approach_marker(
        departure=departure, pattern=leg.pattern, hop=(index, fraction),
        trips=trips, spots=spots, approach=approach, look=look, ways=ways,
    )


def hop_marker(
    *,
    route: str,
    ends: tuple[Spot | None, ...],
    fraction: float,
    age: float | None,
    live: bool,
    known: bool = True,
    way: Way | None = None,
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
    known : bool
        False when the vehicle has no next stop.
    way : Way | None
        The ring or spur stretch the hop runs along, or None on the
        trunk.

    Returns
    -------
    Badge | None
        On a ring or spur, a fixed badge on it heading along it (see
        `way_point`). Else the badge on the later row's track, with a
        wedge down the strip when that row is lower and up when higher,
        none when level or not known; None unless the hop has two ends
        that are both visible rows. Bound for an exit row, it rides the
        earlier row's track until within a row of the exit, then sits on
        the exit's spot as a spur badge, its wedge pointing right along
        the hook.
    """
    if way is not None:
        return way_badge(
            look=Marker(
                route=route, x=0.0, y=0.0, bearing=None, live=live,
                pinned=True, age=age,
            ),
            way=way,
            fraction=fraction,
            known=known,
        )
    if len(ends) != 2:
        return None
    start, end = ends
    if start is None or end is None:
        return None
    y = start.y + (end.y - start.y) * fraction
    spur = end.exit and abs(end.y - y) < ROW_H
    down = None if end.y == start.y else 180.0 if end.y > start.y else 0.0
    bearing = 90.0 if spur else down
    return Badge(
        marker=Marker(
            route=route,
            x=start.x if end.exit and not spur else end.x,
            y=end.y if spur else y,
            bearing=bearing if known else None,
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
        them, so its vehicle gets one badge. A cancelled next trip is not
        among them, so the run still carries on into it.
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


def vehicle_markers(
    *,
    trips: TripInputs,
    keep: Keepouts,
    approach: Approach | None = None,
    ways: Ways | None = None,
) -> tuple[Marker, ...]:
    """
    Place the strip's vehicles on its visible rows.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    keep : Keepouts
        The visible rows' spots, the discs and the top.
    approach : Approach | None
        The rows either side of a both-sides From, or None.
    ways : Ways | None
        The visible rows, rings and spurs, or None.

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
        line_kinds=trips.line_kinds,
    ))
    placed = (
        trip_marker(
            departure=departure, trips=trips, spots=spots, heard=heard,
            approach=approach, ways=ways,
        )
        for departure in departures
    )
    return tuple(
        each.marker
        for each in pushed(
            badges=[each for each in placed if each is not None], keep=keep,
        )
    )
