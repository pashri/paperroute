"""Places, services and departures."""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from itertools import pairwise
from typing import Final

from paperroute_transit.core.geo import Point

TIMETABLE_SCHEMA: Final[str] = '7'
REGIONAL_AGENCIES: Final[frozenset[str]] = frozenset({'711'})
DAY_NAMES: Final[tuple[str, ...]] = (
    'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
)
HIDDEN_HEADSIGNS: Final[frozenset[str]] = frozenset(
    {'Empty Train', 'Out Of Service', 'Non Revenue'},
)


class Mode(StrEnum):
    """Kinds of service."""

    BUS = 'bus'
    TRAIN = 'train'
    METRO = 'metro'
    FERRY = 'ferry'
    LIGHT_RAIL = 'light_rail'
    COACH = 'coach'
    REGIONAL = 'regional'
    OTHER = 'other'


EXACT_MODES: Final[dict[int, Mode]] = {
    2: Mode.TRAIN,
    106: Mode.TRAIN,
    401: Mode.METRO,
    4: Mode.FERRY,
    900: Mode.LIGHT_RAIL,
}


def mode_for(*, route_type: int, agency_id: str | None) -> Mode:
    """
    Find the mode of a route from its GTFS type and agency.

    NSW TrainLink (bundle agency 711) trains and coaches are Regional. TfNSW
    uses extended route types: 700-799 buses, 200-299 coaches.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id, when the feed gives one.

    Returns
    -------
    Mode
        Mode.
    """
    if agency_id in REGIONAL_AGENCIES:
        return Mode.REGIONAL
    if route_type in EXACT_MODES:
        return EXACT_MODES[route_type]
    if 700 <= route_type < 800:
        return Mode.BUS
    return Mode.COACH if 200 <= route_type < 300 else Mode.OTHER


class RouteKind(StrEnum):
    """Kinds of route offered when choosing lines."""

    BUS = 'bus'
    TRAIN = 'train'
    METRO = 'metro'
    LIGHT_RAIL = 'light_rail'
    FERRY = 'ferry'
    SCHOOL = 'school'
    COACH = 'coach'
    REPLACEMENT = 'replacement'


INTERCITY: Final[tuple[str, ...]] = ('BMT', 'CCN', 'SCO', 'SHL', 'HUN')
TRAINLINK: Final[tuple[str, ...]] = ('NRC', 'NRW', 'STH', 'WST')
STATION_FIRST: Final[frozenset[RouteKind]] = frozenset({
    RouteKind.TRAIN,
    RouteKind.METRO,
    RouteKind.LIGHT_RAIL,
    RouteKind.FERRY,
})
SYDNEY_LINES: Final[re.Pattern[str]] = re.compile(r'^T[1-9]$')
SYDNEY_FERRIES: Final[re.Pattern[str]] = re.compile(r'^F(?:[1-9]|10)$')
TRAINLINK_KINDS: Final[dict[int, RouteKind]] = {
    106: RouteKind.TRAIN,
    204: RouteKind.COACH,
    205: RouteKind.COACH,
}
EXACT_KINDS: Final[dict[int, RouteKind]] = {
    **TRAINLINK_KINDS,
    401: RouteKind.METRO,
    900: RouteKind.LIGHT_RAIL,
    4: RouteKind.FERRY,
    700: RouteKind.BUS,
    712: RouteKind.SCHOOL,
    714: RouteKind.REPLACEMENT,
}


def kind_for(
    *, route_type: int, agency_id: str | None, short_name: str,
) -> RouteKind | None:
    """
    Sort a route into a kind from its type, agency and short name.

    Parameters
    ----------
    route_type : int
        GTFS route_type.
    agency_id : str | None
        GTFS agency_id, when the feed gives one.
    short_name : str
        Route short name.

    Returns
    -------
    RouteKind | None
        Kind; None for a route with no short name, a rail line that is
        not a Sydney, intercity or NSW TrainLink line, or an on-demand or
        unknown type. A rail trip keeps its line's kind when merging gave
        it the bundle's NSW TrainLink agency.
    """
    if not short_name:
        return None
    if agency_id in REGIONAL_AGENCIES:
        if route_type == 2:
            return RouteKind.TRAIN if short_name in TRAINLINK else None
        return TRAINLINK_KINDS.get(route_type)
    if route_type == 2:
        known = (
            SYDNEY_LINES.match(short_name)
            or short_name in INTERCITY
            or short_name in TRAINLINK
        )
        return RouteKind.TRAIN if known else None
    return EXACT_KINDS.get(route_type)


def until_repeated_hop(*, stations: Sequence[str]) -> tuple[str, ...]:
    """
    Cut a run before the first hop it has already made.

    A hop is a consecutive pair of stations, in order. A loop that passes a
    station twice is kept whole until it travels between the same two
    stations in the same direction again.

    Parameters
    ----------
    stations : Sequence[str]
        Station IDs in order.

    Returns
    -------
    tuple[str, ...]
        Stations up to and including the start of the repeated hop.
    """
    made: set[tuple[str, str]] = set()
    for index, hop in enumerate(pairwise(stations)):
        if hop in made:
            return tuple(stations[:index + 1])
        made.add(hop)
    return tuple(stations)


def until_revisit(*, stations: Sequence[str]) -> tuple[str, ...]:
    """
    Cut a run before the first station it calls at twice.

    Parameters
    ----------
    stations : Sequence[str]
        Station IDs in order.

    Returns
    -------
    tuple[str, ...]
        Stations before the first repeat.
    """
    seen: set[str] = set()
    for index, station in enumerate(stations):
        if station in seen:
            return tuple(stations[:index])
        seen.add(station)
    return tuple(stations)


@dataclass(frozen=True)
class StopPlace:
    """A stop or station with its position."""

    stop_id: str
    name: str
    point: Point | None


@dataclass(frozen=True, order=True)
class AutoWindow:
    """
    Times of the week when a pin is shown by itself.

    Attributes
    ----------
    days : frozenset[int]
        Days the window starts on, 0 (Monday) to 6 (Sunday).
    start : time
        Local wall-clock time it starts.
    end : time
        Local wall-clock time it ends; before `start` when it runs past
        midnight.
    """

    days: frozenset[int]
    start: time
    end: time


MAX_CHANGES: Final[int] = 2


@dataclass(frozen=True)
class Change:
    """
    Where a way changes from one leg to the next.

    Attributes
    ----------
    alight : StopPlace
        Station or stop where the leg is left.
    board : StopPlace
        Station or stop where the next leg is joined.
    allow : int
        Minutes allowed between the leg arriving and the next leaving.
    """

    alight: StopPlace
    board: StopPlace
    allow: int


@dataclass(frozen=True)
class Way:
    """
    One way to go between a pin's From and Destination.

    Attributes
    ----------
    changes : tuple[Change, ...]
        The changes in order, at most `MAX_CHANGES`.
    ticked : bool
        True when the way is used.
    """

    changes: tuple[Change, ...]
    ticked: bool = True


@dataclass(frozen=True)
class Pin:
    """
    A From place, an optional Destination, and the routes it shows.

    Attributes
    ----------
    origin : StopPlace
        From stop or station.
    destination : StopPlace | None
        Destination, or None for every service leaving the From.
    kind : RouteKind | None
        Kind of route chosen, or None for any.
    routes : tuple[str, ...]
        Route short names chosen; empty for all.
    nickname : str | None
        Name the owner gave the pin, or None.
    only : tuple[str, ...]
        Route short names whose departures are kept; empty for all.
    missing : bool
        True when the timetable no longer knows a place.
    windows : tuple[AutoWindow, ...]
        Times the pin is shown by itself; not part of its identity.
    ways : tuple[Way, ...]
        Ways to go from the From to the Destination; empty for a pin
        that is not a connection. They are not part of its identity.
    never : tuple[str, ...]
        Route short names never used on any leg.
    """

    origin: StopPlace
    destination: StopPlace | None = None
    kind: RouteKind | None = None
    routes: tuple[str, ...] = ()
    nickname: str | None = None
    only: tuple[str, ...] = ()
    missing: bool = False
    windows: tuple[AutoWindow, ...] = ()
    ways: tuple[Way, ...] = ()
    never: tuple[str, ...] = ()


def is_connection(*, pin: Pin) -> bool:
    """
    Say whether a pin has any way to go.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    bool
        True when the pin has at least one way, ticked or not.
    """
    return bool(pin.ways)


def ticked_ways(*, pin: Pin) -> tuple[Way, ...]:
    """
    List the ways a pin uses.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    tuple[Way, ...]
        The ticked ways in order.
    """
    return tuple(way for way in pin.ways if way.ticked)


def way_key(*, way: Way) -> tuple[tuple[str, str], ...]:
    """
    Reduce a way to the stops it changes at.

    Parameters
    ----------
    way : Way
        Way.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Alight and board stop IDs of each change; the allows and the
        tick are not part of it.
    """
    return tuple(
        (change.alight.stop_id, change.board.stop_id)
        for change in way.changes
    )


def same_place(*, first: Pin, second: Pin) -> bool:
    """
    Say whether two pins show the same services.

    Names, points, windows and whether a place is missing are ignored.

    Parameters
    ----------
    first : Pin
        One pin.
    second : Pin
        The other pin.

    Returns
    -------
    bool
        True when the stop IDs, kind, routes and route filter all
        match; the nickname is ignored.
    """
    return pin_key(pin=first) == pin_key(pin=second)


type PinKey = tuple[
    str,
    str | None,
    RouteKind | None,
    tuple[str, ...],
    tuple[str, ...],
]


def pin_key(*, pin: Pin) -> PinKey:
    """
    Reduce a pin to what says which services it shows.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    PinKey
        From stop ID, Destination stop ID or None, kind, routes and
        route filter. A pin with a Destination has no routes or route
        filter in its identity. Ways and the never list are not part of
        it, so editing them keeps the pin's ref.
    """
    destination = pin.destination
    open_ended = destination is None
    return (
        pin.origin.stop_id,
        None if destination is None else destination.stop_id,
        pin.kind,
        pin.routes if open_ended else (),
        pin.only if open_ended else (),
    )


def pin_ref(*, pin: Pin) -> str:
    """
    Give a short, stable name for a pin.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    str
        Twelve lowercase hex digits taken from the pin's identity, so
        two pins that are the same place share a ref. A pin without a
        Destination hashes as it did before ways existed.
    """
    origin, destination, kind, routes, only = pin_key(pin=pin)
    parts: list[object] = [
        origin, destination, kind, list(routes), list(only),
    ]
    text = json.dumps(parts)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class CarryOn:
    """
    The service a vehicle runs next, after this trip.

    Attributes
    ----------
    route : str
        Next trip's route short name.
    headsign : str
        Next trip's headsign.
    trip_id : str
        Next trip's ID, or '' when the timetable doesn't say.
    """

    route: str
    headsign: str
    trip_id: str = ''


@dataclass(frozen=True)
class TripFacts:
    """
    What is known about a departure's trip beyond its times.

    Attributes
    ----------
    kind : RouteKind | None
        Kind of route, or None when not known.
    then : CarryOn | None
        The service the vehicle runs next, or None when not known.
    onto : CarryOn | None
        The trip the vehicle becomes before reaching the Destination,
        when the boarded trip doesn't reach it itself; None otherwise.
    route_id : str
        The timetable's route ID, which tells apart lines sharing a short
        name (T1's Western and North Shore runs); empty when not known.
    """

    kind: RouteKind | None = None
    then: CarryOn | None = None
    onto: CarryOn | None = None
    route_id: str = ''


NO_FACTS: Final[TripFacts] = TripFacts()


@dataclass(frozen=True)
class Departure:
    """
    One direct service leaving a pin's From.

    With a Destination it runs to that Destination; without one, to the
    trip's last stop. `stops` counts the stations it stops at after the
    From up to and including the Destination, across any carry-on; None
    without a Destination or when not known.
    """

    trip_id: str
    aliases: tuple[str, ...]
    route: str
    mode: Mode
    headsign: str
    platform: str
    origin_seq: int
    origin_stop: str
    destination_seq: int
    destination_stop: str
    departs: datetime
    arrives: datetime
    service_day: date
    shape_id: str | None = None
    facts: TripFacts = NO_FACTS
    stops: int | None = None



@dataclass(frozen=True)
class ServiceSpan:
    """
    When a pin's trips run on one service day.

    Attributes
    ----------
    first_start : datetime
        When the earliest of its trips leaves its first stop.
    last_end : datetime
        When the latest of its trips reaches its last stop.
    """

    first_start: datetime
    last_end: datetime


def service_epoch(*, day: date, zone: tzinfo) -> datetime:
    """
    Find the moment GTFS times on a service day count from.

    GTFS measures from noon minus 12 hours, which is midnight except on
    daylight-saving change days (23:00 the evening before when clocks go
    forward, 01:00 when they go back).

    Parameters
    ----------
    day : date
        Service date.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    datetime
        Aware local datetime.
    """
    noon = datetime.combine(day, time(12), tzinfo=zone)
    return (noon.astimezone(UTC) - timedelta(hours=12)).astimezone(zone)


def service_time(*, day: date, seconds: int, zone: tzinfo) -> datetime:
    """
    Turn a GTFS time on a service day into a local time.

    The sum is done in UTC because Python adds to an aware datetime on the
    wall clock, which is wrong across a daylight-saving change.

    Parameters
    ----------
    day : date
        Service date.
    seconds : int
        Seconds after the service day's epoch.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    datetime
        Aware local datetime.
    """
    epoch = service_epoch(day=day, zone=zone).astimezone(UTC)
    return (epoch + timedelta(seconds=seconds)).astimezone(zone)


def shifted(*, moment: datetime, seconds: int, zone: tzinfo) -> datetime:
    """
    Move a moment by real seconds and give the result in local time.

    The sum is done in UTC because Python adds to an aware datetime on the
    wall clock, which is wrong across a daylight-saving change.

    Parameters
    ----------
    moment : datetime
        Aware time.
    seconds : int
        Real seconds to add (negative to subtract).
    zone : tzinfo
        Local time zone.

    Returns
    -------
    datetime
        Aware local datetime.
    """
    return (moment.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(
        zone
    )


def service_seconds(*, day: date, moment: datetime, zone: tzinfo) -> int:
    """
    Count the seconds from a service day's epoch to a moment.

    Parameters
    ----------
    day : date
        Service date.
    moment : datetime
        Aware datetime.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    int
        Elapsed seconds, negative before the epoch.
    """
    epoch = service_epoch(day=day, zone=zone)
    return int(moment.timestamp() - epoch.timestamp())
