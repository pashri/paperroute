"""Tests for trip patterns and timetable positions."""

from datetime import datetime, timedelta

import pytest

from paperroute.core.geo import make_shape
from paperroute.core.model import SYDNEY
from paperroute.core.patterns import PatternStop, measure, timetable_point
from tests.core.factories import EIGHT, START, TEN_MINUTES, pattern

CORNER = make_shape(
    shape_id='L',
    points=[(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)],
)
DIAGONAL = (
    PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
    PatternStop(
        arrives=EIGHT + TEN_MINUTES,
        departs=EIGHT + TEN_MINUTES,
        point=(1000.0, 1000.0),
    ),
)
HALFWAY = START + timedelta(minutes=5)


def test_measure_sets_stop_distances_along_the_shape() -> None:
    """Each stop gets its metres along the shape."""
    result = measure(
        pattern=pattern(shape_id='L', stops=DIAGONAL),
        shape=CORNER,
    )
    assert result.along == pytest.approx((0.0, 2000.0))


def test_measure_without_a_shape_leaves_along_none() -> None:
    """A trip with no shape keeps `along` unset."""
    assert measure(pattern=pattern(), shape=None).along is None


def test_measure_without_stop_points_leaves_along_none() -> None:
    """A stop without coordinates leaves `along` unset."""
    stops = (
        DIAGONAL[0],
        PatternStop(arrives=EIGHT + 60, departs=EIGHT + 60, point=None),
        DIAGONAL[1],
    )
    result = measure(pattern=pattern(shape_id='L', stops=stops), shape=CORNER)
    assert result.along is None


def test_timetable_point_follows_the_shape() -> None:
    """Halfway in time is on the corner path, not the chord."""
    measured = measure(
        pattern=pattern(shape_id='L', stops=DIAGONAL),
        shape=CORNER,
    )
    point = timetable_point(pattern=measured, shape=CORNER, now=HALFWAY)
    assert point == pytest.approx((1000.0, 0.0))


def test_timetable_point_without_shape_uses_the_chord() -> None:
    """With no shape the point is on the straight line between stops."""
    point = timetable_point(
        pattern=pattern(stops=DIAGONAL),
        shape=None,
        now=HALFWAY,
    )
    assert point == pytest.approx((500.0, 500.0))


def test_timetable_point_unmeasured_uses_the_chord() -> None:
    """A shape without stop distances falls back to the chord."""
    point = timetable_point(
        pattern=pattern(shape_id='L', stops=DIAGONAL),
        shape=CORNER,
        now=HALFWAY,
    )
    assert point == pytest.approx((500.0, 500.0))


def test_timetable_point_in_a_dwell_sits_at_the_stop() -> None:
    """Between arriving and departing, the trip is at the stop."""
    stops = (
        DIAGONAL[0],
        PatternStop(
            arrives=EIGHT + 240,
            departs=EIGHT + 420,
            point=(300.0, 700.0),
        ),
        DIAGONAL[1],
    )
    point = timetable_point(
        pattern=pattern(stops=stops),
        shape=None,
        now=HALFWAY,
    )
    assert point == (300.0, 700.0)


def test_timetable_point_after_midnight_uses_previous_service_day() -> None:
    """At 00:30 a trip timed 24:20 to 24:40 runs on yesterday's service."""
    now = datetime(2026, 10, 17, 0, 30, tzinfo=SYDNEY)
    stops = (
        PatternStop(arrives=87_600, departs=87_600, point=(0.0, 0.0)),
        PatternStop(arrives=88_800, departs=88_800, point=(1000.0, 0.0)),
    )
    point = timetable_point(
        pattern=pattern(
            stops=stops,
            days=(now.date() - timedelta(days=1), now.date()),
        ),
        shape=None,
        now=now,
    )
    assert point == pytest.approx((500.0, 0.0))


def test_timetable_point_outside_the_trip_is_none() -> None:
    """Before the first departure and after the last arrival give None."""
    trip = pattern()
    early = START - timedelta(minutes=1)
    late = START + timedelta(minutes=11)
    assert timetable_point(pattern=trip, shape=None, now=early) is None
    assert timetable_point(pattern=trip, shape=None, now=late) is None


def test_timetable_point_without_stop_points_is_none() -> None:
    """A trip between stops without coordinates has no point."""
    stops = (
        PatternStop(arrives=EIGHT, departs=EIGHT, point=None),
        DIAGONAL[1],
    )
    point = timetable_point(
        pattern=pattern(stops=stops),
        shape=None,
        now=HALFWAY,
    )
    assert point is None


def test_timetable_point_at_the_last_arrival_is_the_last_stop() -> None:
    """At the last arrival time the trip is at the last stop."""
    point = timetable_point(
        pattern=pattern(),
        shape=None,
        now=START + timedelta(minutes=10),
    )
    assert point == (1000.0, 0.0)


def test_timetable_point_without_stops_is_none() -> None:
    """A trip with no calls has no point."""
    point = timetable_point(
        pattern=pattern(stops=()),
        shape=None,
        now=HALFWAY,
    )
    assert point is None
