"""Where the hero's vehicle is on its run, and the words for it."""

from dataclasses import dataclass
from datetime import date, tzinfo
from typing import Final

from paperroute_device.core.text import clock_text
from paperroute_transit.core.model import (
    Departure,
    service_seconds,
    service_time,
)
from paperroute_transit.core.names import base_name
from paperroute_transit.core.patterns import TripPattern, hop_near
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.stop_under import GaugeCall, StopGauge
from paperroute_transit.core.stop_view import stops_text

AT_STOP: Final[str] = 'at the stop now'
WAITING: Final[str] = 'waiting here'
NOT_LEFT: Final[str] = 'not left yet'
BACK_MOST: Final[int] = 3  # previous stops named before a break


@dataclass(frozen=True)
class BusSeen:
    """
    A vehicle's place on the way to the From.

    Attributes
    ----------
    left : int
        Pattern index of the call it last left or waits at.
    away : int
        Calls still to make, up to and including the From.
    waiting : bool
        True when it waits at the first stop of the trip.
    """

    left: int
    away: int
    waiting: bool


def bus_seen(
    *,
    pattern: TripPattern,
    start: int,
    vehicle: VehiclePosition,
    departed: bool,
) -> BusSeen | None:
    """
    Place a vehicle among the calls before the From.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : int
        Pattern index of the From.
    vehicle : VehiclePosition
        The vehicle's reported position.
    departed : bool
        Whether the trip has left its first stop.

    Returns
    -------
    BusSeen | None
        Its place, or None when it has no hop or is past the From.
    """
    hop = hop_near(pattern=pattern, point=vehicle.point)
    if hop is None:
        return None
    waiting = hop[0] == 0 and not departed
    if hop[0] >= start and not (start == 0 and waiting):
        return None
    seen = 0 if waiting else hop[0] + int(hop[1] >= 1.0)
    stops = pattern.stops
    left = next(
        (
            index
            for index in range(seen, -1, -1)
            if not stops[index].passes
        ),
        seen,
    )
    away = sum(not stop.passes for stop in stops[left + 1 : start + 1])
    return BusSeen(left=left, away=away, waiting=waiting)


def bus_words(*, bus: BusSeen) -> str:
    """
    Say how far a vehicle is from the From.

    Parameters
    ----------
    bus : BusSeen
        Where the vehicle is.

    Returns
    -------
    str
        Such as '2 stops away' or 'waiting here'.
    """
    if bus.away == 0:
        return WAITING if bus.waiting else AT_STOP
    words = f'{stops_text(stops=bus.away)} away'
    return f'{words}, {NOT_LEFT}' if bus.waiting else words


def arrival_call(
    *,
    pattern: TripPattern,
    departure: Departure,
    start: int,
    station: str | None = None,
    zone: tzinfo,
) -> int | None:
    """
    Find the call where a departure's trip ends its ride.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    departure : Departure
        The departure, whose `arrives` names the Destination's time.
    start : int
        Pattern index of the From.
    station : str | None
        The Destination's station, or None when not known.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    int | None
        Pattern index of the first later call at the Destination's
        station arriving then, else the first at that station, else the
        first later call arriving then, or None.
    """
    seconds = service_seconds(
        day=departure.service_day, moment=departure.arrives, zone=zone,
    )
    later = [
        i
        for i in range(start + 1, len(pattern.stops))
        if not pattern.stops[i].passes
    ]
    timed = [i for i in later if pattern.stops[i].arrives == seconds]
    there = [i for i in later if pattern.stops[i].station == station]
    exact = [i for i in timed if i in there]
    return next(iter(exact or there or timed), None)


def gauge_calls(
    *,
    pattern: TripPattern,
    start: int,
    end: int | None,
    day: date,
    delay: int,
    zone: tzinfo,
) -> tuple[tuple[GaugeCall, ...], int | None]:
    """
    List the marks after the From, with the Destination's place.

    Passing stops, unnamed stops and repeats of the call before are
    skipped, except the Destination.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.
    day : date
        Service date.
    delay : int
        Seconds late.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    tuple[tuple[GaugeCall, ...], int | None]
        The marks and the Destination's index among them.
    """
    calls: list[GaugeCall] = []
    place = None
    for index in range(start + 1, len(pattern.stops)):
        stop = pattern.stops[index]
        name = base_name(name=stop.name)
        repeat = bool(calls) and calls[-1].name == name
        if index != end and (stop.passes or not stop.name or repeat):
            continue
        moment = service_time(
            day=day, seconds=stop.arrives + delay, zone=zone,
        )
        minutes = clock_text(moment=moment, zone=zone)[-3:]
        calls.append(GaugeCall(name=name, minutes=minutes))
        if index == end:
            place = len(calls) - 1
    return tuple(calls), place


def gauge_behind(
    *, pattern: TripPattern, start: int, bus: BusSeen,
) -> tuple[tuple[str, ...], int]:
    """
    Name the stops the vehicle has passed before the From.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : int
        Pattern index of the From.
    bus : BusSeen
        Where the vehicle is.

    Returns
    -------
    tuple[tuple[str, ...], int]
        The names and the number of stops a back break stands for.
    """
    names = tuple(
        base_name(name=stop.name)
        for stop in pattern.stops[bus.left : start]
        if not stop.passes
    )
    if bus.away > BACK_MOST:
        return names[:1], bus.away - 1
    return names, 0


def stop_gauge(
    *,
    pattern: TripPattern,
    start: int,
    end: int | None,
    bus: BusSeen | None,
    day: date,
    delay: int,
    zone: tzinfo,
) -> StopGauge:
    """
    Work out everything the Stop view's gauge shows.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : int
        Pattern index of the From.
    end : int | None
        Pattern index of the Destination.
    bus : BusSeen | None
        Where the vehicle is, or None when not reported.
    day : date
        Service date.
    delay : int
        Seconds late.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    StopGauge
        The gauge's content.
    """
    calls, place = gauge_calls(
        pattern=pattern, start=start, end=end, day=day, delay=delay,
        zone=zone,
    )
    origin = base_name(name=pattern.stops[start].name)
    if bus is None:
        return StopGauge(
            origin=origin, calls=calls, end=place, starts=start == 0,
        )
    behind, hidden = gauge_behind(pattern=pattern, start=start, bus=bus)
    return StopGauge(
        origin=origin,
        behind=behind,
        hidden=hidden,
        bus=0.0 if bus.waiting or bus.away == 0 else 0.5,
        calls=calls,
        end=place,
        starts=bus.left == 0 or (not behind and start == 0),
        words=bus_words(bus=bus),
    )
