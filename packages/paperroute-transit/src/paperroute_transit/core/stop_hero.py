"""Where the Stop view's next vehicle was last seen, and its next calls."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Final

from paperroute_device.core.text import clock_text
from paperroute_transit.core.connection_strip import leg_vehicle
from paperroute_transit.core.model import service_time
from paperroute_transit.core.patterns import TripPattern, hop_near
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.stop_under import NextCall
from paperroute_transit.core.stop_view import (
    StopView,
    hero_departure,
    stops_text,
)
from paperroute_transit.core.strip_times import from_call

CALL_COUNT: Final[int] = 3  # calls shown after the From
STATION_SUFFIX: Final[str] = ' Station'
HERE: Final[str] = 'at the stop now'


def short_name(*, name: str) -> str:
    """
    Drop the ' Station' suffix from a stop name.

    Parameters
    ----------
    name : str
        Stop name.

    Returns
    -------
    str
        The name without a trailing ' Station'.
    """
    return name.removesuffix(STATION_SUFFIX)


def hero_place(
    *, pattern: TripPattern, start: int, vehicle: VehiclePosition,
) -> str:
    """
    Say where the hero's vehicle was last seen and how far it has to go.

    Parameters
    ----------
    pattern : TripPattern
        The hero's trip.
    start : int
        The index of the call at the From.
    vehicle : VehiclePosition
        The vehicle's reported position.

    Returns
    -------
    str
        '{stops_text} away, last seen at {name}' counting the calls
        still to make up to and including the From, or 'at the stop now'
        when none is left to count; '' when the vehicle has no hop or its
        hop is at or past the From.
    """
    hop = hop_near(pattern=pattern, point=vehicle.point)
    if hop is None or hop[0] >= start:
        return ''
    seen = hop[0] + int(hop[1] >= 1.0)
    name = short_name(name=pattern.stops[seen].name)
    count = sum(
        not stop.passes for stop in pattern.stops[seen + 1 : start + 1]
    )
    if count == 0:
        return HERE
    if not name:
        return ''
    return f'{stops_text(stops=count)} away, last seen at {name}'


def next_calls(
    *,
    pattern: TripPattern,
    start: int,
    day: date,
    delay: int,
    zone: tzinfo,
) -> tuple[NextCall, ...]:
    """
    List the stops after the From with their times moved by the delay.

    Parameters
    ----------
    pattern : TripPattern
        The hero's trip.
    start : int
        The index of the call at the From.
    day : date
        The departure's service day.
    delay : int
        Seconds late, negative when early.
    zone : tzinfo
        Local time zone the times show in.

    Returns
    -------
    tuple[NextCall, ...]
        Up to `CALL_COUNT` calls, skipping stops the trip passes, stops
        with no name and a stop named as the call before it.
    """
    calls: list[NextCall] = []
    before = short_name(name=pattern.stops[start].name)
    for stop in pattern.stops[start + 1 :]:
        name = short_name(name=stop.name)
        if len(calls) == CALL_COUNT:
            break
        if stop.passes or not name or name == before:
            continue
        moment = service_time(
            day=day, seconds=stop.arrives + delay, zone=zone,
        )
        at = clock_text(moment=moment, zone=zone)
        calls.append(NextCall(at=at, name=name))
        before = name
    return tuple(calls)


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
    Add where the hero was last seen and its next calls to a Stop view.

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
        The same view when it has no block under the number, no hero, no
        loaded pattern or no call at the From. Otherwise the block gains
        the calls, and a place only when the hero's own trip has a fresh
        reported position.
    """
    departure = hero_departure(view=view)
    pattern = None if departure is None else patterns.get(departure.trip_id)
    if view.under is None or departure is None or pattern is None:
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
    seen = leg_vehicle(
        departure=departure, positions=positions, now=now, limit=limit,
    )
    under = replace(
        view.under,
        place='' if seen is None else hero_place(
            pattern=pattern, start=start, vehicle=seen,
        ),
        calls=next_calls(
            pattern=pattern,
            start=start,
            day=departure.service_day,
            delay=view.under.delay,
            zone=zone,
        ),
    )
    return replace(view, under=under)
