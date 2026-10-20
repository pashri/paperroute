"""Where a strip's vehicles are drawn."""

from collections.abc import Mapping

from paperroute.core.map_view import (
    Marker,
    is_fresh,
    position_age,
    timetable_moment,
)
from paperroute.core.model import Departure, service_seconds
from paperroute.core.patterns import hop_at, hop_near
from paperroute.core.positions import VehiclePosition
from paperroute.core.strip_badges import Badge, Keepouts, Spot, pushed
from paperroute.core.strip_model import ROW_H
from paperroute.core.strip_times import TripInputs, window_departures

type Heard = Mapping[str, tuple[VehiclePosition, float]]


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
        at the timetable hop; None without a pattern, a hop, or both of
        the hop's rows.
    """
    pattern = trips.patterns.get(departure.trip_id)
    if pattern is None:
        return None
    found = vehicle_of(departure=departure, heard=heard)
    fresh = found is not None and is_fresh(age=found[1])
    if found is not None and fresh:
        hop = hop_near(pattern=pattern, point=found[0].point)
    else:
        moment = timetable_moment(now=trips.now)
        hop = hop_at(pattern=pattern, seconds=service_seconds(
            day=departure.service_day, moment=moment,
        ))
    ends = None if hop is None else tuple(
        spots.get(trips.station_of.get(stop.station, stop.station))
        for stop in pattern.stops[hop[0]:hop[0] + 2]
    )
    age = None if found is None or fresh else found[1] // 60 * 60
    return hop_marker(
        route=pattern.route,
        ends=ends,
        fraction=0.0 if hop is None else hop[1],
        age=age,
        live=fresh,
    )


def hop_marker(
    *,
    route: str,
    ends: tuple[Spot | None, ...] | None,
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
    ends : tuple[Spot | None, ...] | None
        The spots of the hop's two stops, each None when not a visible
        row; None with no hop.
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
    if ends is None:
        return None
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
        each other (see `pushed`).
    """
    key = trips.key
    spots = keep.spots
    if key is None or not spots:
        return ()
    heard = heard_trips(trips=trips)
    departures = window_departures(
        departures=[
            item.departure for item in trips.departures if not item.cancelled
        ],
        key=key,
        now=trips.now,
    )
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
