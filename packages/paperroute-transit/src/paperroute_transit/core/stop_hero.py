"""Add the gauge of the stops ahead to the Stop view."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, tzinfo

from paperroute_transit.core.connection_strip import leg_vehicle
from paperroute_transit.core.gauge import (
    arrival_call,
    bus_seen,
    bus_words,
    gauge_calls,
)
from paperroute_transit.core.gauge_buses import gauge_buses
from paperroute_transit.core.gauge_ends import RouteGauge
from paperroute_transit.core.gauge_requests import GaugeData
from paperroute_transit.core.map_view import is_fresh, position_age
from paperroute_transit.core.model import Departure, service_time
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.route_trips import running
from paperroute_transit.core.stop_view import StopView, hero_departure
from paperroute_transit.core.strip_times import from_call


def hero_gauge(
    *,
    view: StopView,
    departure: Departure,
    pattern: TripPattern,
    start: int,
    vehicle: VehiclePosition | None,
    now: datetime,
    zone: tzinfo,
    destination: str | None = None,
) -> RouteGauge:
    """
    Work out the gauge for the hero's trip.

    Parameters
    ----------
    view : StopView
        The view, whose delay and Destination are used.
    departure : Departure
        The hero's departure.
    pattern : TripPattern
        The hero's trip.
    start : int
        Pattern index of the From.
    vehicle : VehiclePosition | None
        The hero's vehicle, or None when not reported.
    now : datetime
        The frame time.
    zone : tzinfo
        Local time zone.
    destination : str | None
        The Destination's station, or None when not known.

    Returns
    -------
    RouteGauge
        The gauge with no buses, ending at the Destination when the
        view has one.
    """
    day = departure.service_day
    departed = now >= service_time(
        day=day, seconds=pattern.stops[0].departs + view.delay, zone=zone,
    )
    bus = None if vehicle is None else bus_seen(
        pattern=pattern, start=start, vehicle=vehicle, departed=departed,
    )
    end = None if view.destination is None else arrival_call(
        pattern=pattern,
        departure=departure,
        start=start,
        station=destination,
        zone=zone,
    )
    calls, _ = gauge_calls(
        pattern=pattern,
        start=start,
        end=end,
        day=day,
        delay=view.delay,
        zone=zone,
    )
    return RouteGauge(
        pattern=pattern,
        start=start,
        end=end,
        calls=calls,
        buses=(),
        words='' if bus is None else bus_words(bus=bus),
    )


def route_vehicles(
    *,
    pattern: TripPattern,
    positions: Mapping[str, PositionsSnapshot],
    now: datetime,
    limit: timedelta,
) -> tuple[VehiclePosition, ...]:
    """
    List the fresh reports of vehicles on a trip's route.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.
    now : datetime
        The frame time.
    limit : timedelta
        How old a position may be.

    Returns
    -------
    tuple[VehiclePosition, ...]
        The vehicles; none when the trip's route is not known.
    """
    return tuple(
        item
        for feed in positions.values()
        for item in feed.vehicles
        if pattern.route_id is not None
        and item.route_id == pattern.route_id
        and is_fresh(
            age=position_age(vehicle=item, snapshot=feed, now=now),
            limit=limit,
        )
    )


def with_detail(
    *,
    view: StopView,
    station: str,
    patterns: Mapping[str, TripPattern | None],
    gauge: GaugeData,
    now: datetime,
    limit: timedelta,
    zone: tzinfo,
    destination: str | None = None,
) -> StopView:
    """
    Add the gauge of the stops ahead to a Stop view.

    Parameters
    ----------
    view : StopView
        The view.
    station : str
        The From's station: its parent station, or the From itself.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by trip ID.
    gauge : GaugeData
        The positions and route trips the gauge reads.
    now : datetime
        The frame time.
    limit : timedelta
        How old a position may be.
    zone : tzinfo
        Local time zone the times show in.
    destination : str | None
        The Destination's station, or None when the pin has none.

    Returns
    -------
    StopView
        The same view when it has no hero, no loaded pattern or no call
        at the From. Otherwise the view gains its gauge, and its block
        under the number, if any, gains the words for the vehicle.
    """
    departure = hero_departure(view=view)
    pattern = None if departure is None else patterns.get(departure.trip_id)
    if departure is None or pattern is None:
        return view
    start = from_call(
        departure=departure,
        pattern=pattern,
        origin=station,
        stations={},
        zone=zone,
    )
    if start is None:
        return view
    built = hero_gauge(
        view=view,
        departure=departure,
        pattern=pattern,
        start=start,
        vehicle=leg_vehicle(
            departure=departure,
            positions=gauge.positions,
            now=now,
            limit=limit,
        ),
        now=now,
        zone=zone,
        destination=destination,
    )
    built = replace(
        built,
        buses=gauge_buses(
            hero=pattern,
            patterns=patterns,
            vehicles=route_vehicles(
                pattern=pattern,
                positions=gauge.positions,
                now=now,
                limit=limit,
            ),
            trips_running=running(trips=gauge.trips, now=now, zone=zone),
            now=now,
            zone=zone,
        ),
    )
    under = view.under
    if under is not None:
        under = replace(under, place=built.words)
    return replace(view, under=under, gauge=built)
