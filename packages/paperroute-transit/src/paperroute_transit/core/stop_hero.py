"""Add the gauge of the stops ahead to the Stop view."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, tzinfo

from paperroute_transit.core.connection_strip import leg_vehicle
from paperroute_transit.core.gauge import arrival_call, bus_seen, stop_gauge
from paperroute_transit.core.model import Departure, service_time
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.stop_under import StopGauge
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
) -> StopGauge:
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

    Returns
    -------
    StopGauge
        The gauge, ending at the Destination when the view has one.
    """
    day = departure.service_day
    departed = now >= service_time(
        day=day, seconds=pattern.stops[0].departs + view.delay, zone=zone,
    )
    bus = None if vehicle is None else bus_seen(
        pattern=pattern, start=start, vehicle=vehicle, departed=departed,
    )
    end = None if view.destination is None else arrival_call(
        pattern=pattern, departure=departure, start=start, zone=zone,
    )
    return stop_gauge(
        pattern=pattern,
        start=start,
        end=end,
        bus=bus,
        day=day,
        delay=view.delay,
        zone=zone,
    )


def with_detail(
    *,
    view: StopView,
    station: str,
    patterns: Mapping[str, TripPattern | None],
    positions: Mapping[str, PositionsSnapshot],
    now: datetime,
    limit: timedelta,
    zone: tzinfo,
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
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.
    now : datetime
        The frame time.
    limit : timedelta
        How old a position may be.
    zone : tzinfo
        Local time zone the times show in.

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
    vehicle = leg_vehicle(
        departure=departure, positions=positions, now=now, limit=limit,
    )
    gauge = hero_gauge(
        view=view,
        departure=departure,
        pattern=pattern,
        start=start,
        vehicle=vehicle,
        now=now,
        zone=zone,
    )
    under = view.under
    if under is not None:
        under = replace(under, place=gauge.words)
    return replace(view, under=under, gauge=gauge)
