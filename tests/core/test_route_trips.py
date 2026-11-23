"""Tests for the route's trips and which are running."""

from datetime import date, datetime, timedelta
from typing import Final

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_transit.core.model import service_time
from paperroute_transit.core.route_trips import RouteTrip, running

ZONE: Final = SYDNEY_ZONE
TODAY: Final[date] = date(2026, 10, 16)
YESTERDAY: Final[date] = TODAY - timedelta(days=1)
EIGHT: Final[int] = 8 * 3600  # seconds


def trip(
    trip_id: str, *, starts: int, ends: int, day: date = TODAY
) -> RouteTrip:
    """
    A trip of the route.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    starts : int
        First departure in seconds into the service day.
    ends : int
        Last arrival in seconds into the service day.
    day : date
        Service day.

    Returns
    -------
    RouteTrip
        The trip.
    """
    return RouteTrip(
        trip_id=trip_id, day=day, starts=starts, ends=ends, direction=0,
    )


def at(seconds: int, *, day: date = TODAY) -> datetime:
    """
    A moment on a service day.

    Parameters
    ----------
    seconds : int
        Seconds into the service day.
    day : date
        Service day.

    Returns
    -------
    datetime
        Aware local time.
    """
    return service_time(day=day, seconds=seconds, zone=ZONE)


def test_running_keeps_trips_between_first_departure_and_last_arrival(
) -> None:
    """Only the trip under way at the moment is kept."""
    trips = (
        trip('a', starts=EIGHT, ends=EIGHT + 1800),
        trip('b', starts=EIGHT + 3600, ends=EIGHT + 5400),
    )
    found = running(trips=trips, now=at(EIGHT + 60), zone=ZONE)
    assert [t.trip_id for t in found] == ['a']


def test_running_keeps_a_trip_past_midnight() -> None:
    """A trip of the day before still runs after midnight."""
    late = trip(
        'n', day=YESTERDAY, starts=24 * 3600 + 600, ends=25 * 3600 + 600,
    )
    assert running(trips=(late,), now=at(1200), zone=ZONE) == (late,)


def test_running_drops_a_trip_at_its_end_plus_one_second() -> None:
    """The last arrival is included and the next second is not."""
    only = trip('a', starts=EIGHT, ends=EIGHT + 1800)
    assert running(trips=(only,), now=at(EIGHT + 1800), zone=ZONE) == (only,)
    assert not running(trips=(only,), now=at(EIGHT + 1801), zone=ZONE)
