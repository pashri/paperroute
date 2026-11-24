"""Tests for the stops a departure is ridden between."""

from datetime import timedelta
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    pattern,
)
from paperroute_transit.core.geo import Point
from paperroute_transit.core.model import Departure
from paperroute_transit.core.patterns import PatternStop
from paperroute_transit.core.ridden import (
    APPROACH_AFTER,
    APPROACH_BEFORE,
    NO_DESTINATION_WINDOW,
    ridden_stops,
    route_stops,
    stretch_stops,
)

ZONE: Final[ZoneInfo] = city_under_test().zone
POINTS = {name: (index * 500.0, 0.0) for index, name in enumerate('ABCDEFG')}


def test_ridden_stops_between_board_and_alight() -> None:
    """Stops before the board and after the alight are left out."""
    stops = calls(stations='ABCDEFG', step=600, points=POINTS)
    ride = departure(departs=START + timedelta(minutes=10), ride=30)
    assert ridden_stops(
        departure=ride,
        pattern=pattern(stops=stops),
        window=None,
        zone=ZONE,
    ) == (POINTS['B'], POINTS['C'], POINTS['D'], POINTS['E'])


def test_ridden_stops_window_cuts_at_20_min() -> None:
    """With a window, stops reached after it are left out."""
    stops = calls(stations='ABCDEFG', step=600, points=POINTS)
    ride = departure(departs=START, ride=60)
    assert NO_DESTINATION_WINDOW == timedelta(minutes=20)
    assert ridden_stops(
        departure=ride,
        pattern=pattern(stops=stops),
        window=NO_DESTINATION_WINDOW,
        zone=ZONE,
    ) == (POINTS['A'], POINTS['B'], POINTS['C'])


def test_ridden_stops_skip_stops_without_points() -> None:
    """A stop with no point, or no pattern, gives nothing for it."""
    stops = calls(
        stations='ABC',
        step=600,
        points={'A': POINTS['A'], 'C': POINTS['C']},
    )
    ride = departure(departs=START, ride=30)
    assert ridden_stops(
        departure=ride, pattern=pattern(stops=stops), window=None,
        zone=ZONE,
    ) == (POINTS['A'], POINTS['C'])
    assert not ridden_stops(
        departure=ride,
        pattern=None,
        window=None,
        zone=ZONE,
    )


def test_route_stops_once_each() -> None:
    """Departures sharing stops list each stop once, in order."""
    first = departure(departs=START, ride=20, trip_id='t1')
    second = departure(
        departs=START + timedelta(minutes=10), ride=20, trip_id='t2',
    )
    stops = calls(stations='ABCDEFG', step=600, points=POINTS)
    later = calls(
        stations='ABCDEFG',
        start=EIGHT + 600,
        step=600,
        points=POINTS,
    )
    found = route_stops(
        departures=(first, second),
        patterns={
            't1': pattern(trip_id='t1', stops=stops),
            't2': pattern(trip_id='t2', stops=later),
            'other': None,
        },
        window=None,
        zone=ZONE,
    )
    assert found == (POINTS['A'], POINTS['B'], POINTS['C'])


# Calls every 3 minutes from 8:00: A 8:00, B 8:03 ... F 8:15, G 8:18.
EVERY_THREE: Final = calls(
    stations='ABCDEFG', step=180, points=POINTS,
)


def approach(
    *, ride: Departure, stops: tuple[PatternStop, ...],
) -> tuple[Point, ...]:
    """
    List a departure's stops from 10 minutes before it to 3 after.

    Parameters
    ----------
    ride : Departure
        The departure.
    stops : tuple[PatternStop, ...]
        Its pattern's calls.

    Returns
    -------
    tuple[Point, ...]
        The stop points.
    """
    return ridden_stops(
        departure=ride,
        pattern=pattern(stops=stops),
        window=APPROACH_AFTER,
        zone=ZONE,
        before=APPROACH_BEFORE,
    )


def test_ridden_stops_reach_back_ten_minutes_and_on_three() -> None:
    """From E at 8:12 the stretch is B at 8:02 or later to F at 8:15."""
    assert APPROACH_BEFORE == timedelta(minutes=10)
    assert APPROACH_AFTER == timedelta(minutes=3)
    ride = departure(departs=START + timedelta(minutes=12), ride=30)
    assert approach(ride=ride, stops=EVERY_THREE) == tuple(
        POINTS[name] for name in 'BCDEF'
    )


def test_ridden_stops_reach_back_exactly_ten_minutes() -> None:
    """A call exactly 10 minutes before is kept; a second earlier is not."""
    ride = departure(departs=START + timedelta(minutes=10), ride=30)
    exact = calls(stations='ABC', step=300, points=POINTS)
    early = calls(stations='ABC', start=EIGHT - 1, step=300, points=POINTS)
    assert approach(ride=ride, stops=exact) == tuple(
        POINTS[name] for name in 'ABC'
    )
    assert approach(ride=ride, stops=early) == (POINTS['B'], POINTS['C'])


def test_ridden_stops_reach_back_only_as_far_as_the_trip() -> None:
    """A trip starting 6 minutes before the stop gives what it has."""
    ride = departure(departs=START + timedelta(minutes=6), ride=30)
    assert approach(ride=ride, stops=EVERY_THREE) == tuple(
        POINTS[name] for name in 'ABCD'
    )


def test_stretch_stops_of_one_route_are_one_stretch() -> None:
    """Two trips of one pattern ten minutes apart give the same stops once."""
    later = calls(
        stations='ABCDEFG', start=EIGHT + 600, step=180, points=POINTS,
    )
    first = departure(
        departs=START + timedelta(minutes=12), ride=30, trip_id='t1',
    )
    second = departure(
        departs=START + timedelta(minutes=22), ride=30, trip_id='t2',
    )
    assert stretch_stops(
        departures=(first, second),
        patterns={
            't1': pattern(trip_id='t1', stops=EVERY_THREE),
            't2': pattern(trip_id='t2', stops=later),
        },
        zone=ZONE,
    ) == tuple(POINTS[name] for name in 'BCDEF')
