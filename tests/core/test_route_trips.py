"""Tests for the route's trips and which are running."""

from datetime import date, datetime, timedelta
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_transit.core.gauge_buses import forget_held
from paperroute_transit.core.model import service_time
from paperroute_transit.core.route_trips import SPANS, RouteTrip, running

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


@pytest.fixture(name='_fresh', autouse=True)
def _fresh_fixture() -> None:
    """Start each test with nothing remembered."""
    forget_held()


def test_running_works_spans_out_once_for_the_same_trips() -> None:
    """Later ticks on the same trips reuse the spans and stay right."""
    trips = (
        trip('a', starts=EIGHT, ends=EIGHT + 1800),
        trip('b', starts=EIGHT + 3600, ends=EIGHT + 5400),
    )
    assert running(trips=trips, now=at(EIGHT + 60), zone=ZONE) == trips[:1]
    assert running(trips=trips, now=at(EIGHT + 3660), zone=ZONE) == trips[1:]
    assert len(SPANS.held) == 1


def test_running_reads_trips_reloaded_as_new_objects() -> None:
    """Reloaded trips with new times are not answered from the old ones."""
    first = (trip('a', starts=EIGHT, ends=EIGHT + 1800),)
    moved = (trip('a', starts=EIGHT + 3600, ends=EIGHT + 5400),)
    now = at(EIGHT + 60)
    assert running(trips=first, now=now, zone=ZONE) == first
    assert not running(trips=moved, now=now, zone=ZONE)
    assert running(trips=moved, now=at(EIGHT + 3660), zone=ZONE) == moved
