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
from paperroute_transit.core.ridden import (
    NO_DESTINATION_WINDOW,
    ridden_stops,
    route_stops,
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
    assert ridden_stops(
        departure=ride,
        pattern=None,
        window=None,
        zone=ZONE,
    ) == ()


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
