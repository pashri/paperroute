"""Places, services and departures."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

SYDNEY: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
REGIONAL_AGENCIES: Final[frozenset[str]] = frozenset({'711'})


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


@dataclass(frozen=True)
class Pin:
    """A From stop or station and a Destination."""

    origin: str
    destination: str


@dataclass(frozen=True)
class Departure:
    """One direct service from a pin's From to its Destination."""

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


def service_epoch(*, day: date) -> datetime:
    """
    Find the moment GTFS times on a service day count from.

    GTFS measures from noon minus 12 hours, which is midnight except on
    daylight-saving change days (23:00 the evening before when clocks go
    forward, 01:00 when they go back).

    Parameters
    ----------
    day : date
        Service date.

    Returns
    -------
    datetime
        Aware Sydney datetime.
    """
    noon = datetime.combine(day, time(12), tzinfo=SYDNEY)
    return (noon.astimezone(UTC) - timedelta(hours=12)).astimezone(SYDNEY)


def service_time(*, day: date, seconds: int) -> datetime:
    """
    Turn a GTFS time on a service day into a Sydney time.

    The sum is done in UTC because Python adds to an aware datetime on the
    wall clock, which is wrong across a daylight-saving change.

    Parameters
    ----------
    day : date
        Service date.
    seconds : int
        Seconds after the service day's epoch.

    Returns
    -------
    datetime
        Aware Sydney datetime.
    """
    epoch = service_epoch(day=day).astimezone(UTC)
    return (epoch + timedelta(seconds=seconds)).astimezone(SYDNEY)


def service_seconds(*, day: date, moment: datetime) -> int:
    """
    Count the seconds from a service day's epoch to a moment.

    Parameters
    ----------
    day : date
        Service date.
    moment : datetime
        Aware datetime.

    Returns
    -------
    int
        Elapsed seconds, negative before the epoch.
    """
    return int(moment.timestamp() - service_epoch(day=day).timestamp())
