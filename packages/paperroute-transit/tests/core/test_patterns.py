"""Tests for trip patterns and timetable positions."""

from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    TEN_MINUTES,
    pattern,
)
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.patterns import (
    PatternStop,
    hop_at,
    hop_near,
    measure,
    point_at,
    timetable_point,
)

ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
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
QUARTER = START + timedelta(minutes=2, seconds=30)


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
    point = timetable_point(
        pattern=measured,
        shape=CORNER,
        now=HALFWAY,
        zone=ZONE,
    )
    assert point == pytest.approx((1000.0, 0.0))


def test_timetable_point_without_shape_uses_the_chord() -> None:
    """With no shape the point is on the straight line between stops."""
    point = timetable_point(
        pattern=pattern(stops=DIAGONAL),
        shape=None,
        now=HALFWAY,
        zone=ZONE,
    )
    assert point == pytest.approx((500.0, 500.0))


def test_timetable_point_unmeasured_uses_the_chord() -> None:
    """A shape without stop distances falls back to the chord."""
    point = timetable_point(
        pattern=pattern(shape_id='L', stops=DIAGONAL),
        shape=CORNER,
        now=HALFWAY,
        zone=ZONE,
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
        zone=ZONE,
    )
    assert point == (300.0, 700.0)


def test_timetable_point_after_midnight_uses_previous_service_day() -> None:
    """At 00:30 a trip timed 24:20 to 24:40 runs on yesterday's service."""
    now = datetime(2026, 10, 17, 0, 30, tzinfo=ZONE)
    stops = (
        PatternStop(arrives=87_600, departs=87_600, point=(0.0, 0.0)),
        PatternStop(arrives=88_800, departs=88_800, point=(1000.0, 0.0)),
    )
    point = timetable_point(
        pattern=pattern(
            stops=stops,
            days=(now.date(), now.date() - timedelta(days=1)),
        ),
        shape=None,
        now=now,
        zone=ZONE,
    )
    assert point == pytest.approx((500.0, 0.0))


def test_timetable_point_outside_the_trip_is_none() -> None:
    """Before the first departure and after the last arrival give None."""
    trip = pattern()
    early = START - timedelta(minutes=1)
    late = START + timedelta(minutes=11)
    assert timetable_point(
        pattern=trip,
        shape=None,
        now=early,
        zone=ZONE,
    ) is None
    assert timetable_point(
        pattern=trip,
        shape=None,
        now=late,
        zone=ZONE,
    ) is None


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
        zone=ZONE,
    )
    assert point is None


def test_timetable_point_at_the_last_arrival_is_the_last_stop() -> None:
    """At the last arrival time the trip is at the last stop."""
    point = timetable_point(
        pattern=pattern(),
        shape=None,
        now=START + timedelta(minutes=10),
        zone=ZONE,
    )
    assert point == (1000.0, 0.0)


def test_timetable_point_without_stops_is_none() -> None:
    """A trip with no calls has no point."""
    point = timetable_point(
        pattern=pattern(stops=()),
        shape=None,
        now=HALFWAY,
        zone=ZONE,
    )
    assert point is None


def test_timetable_point_quarter_hop_runs_forwards_on_the_chord() -> None:
    """A quarter of the hop in time is a quarter of the way from the start."""
    point = timetable_point(
        pattern=pattern(stops=DIAGONAL),
        shape=None,
        now=QUARTER,
        zone=ZONE,
    )
    assert point == pytest.approx((250.0, 250.0))


def test_timetable_point_quarter_hop_runs_forwards_on_the_shape() -> None:
    """A quarter of the hop in time is a quarter of the way along the shape."""
    measured = measure(
        pattern=pattern(shape_id='L', stops=DIAGONAL),
        shape=CORNER,
    )
    point = timetable_point(
        pattern=measured,
        shape=CORNER,
        now=QUARTER,
        zone=ZONE,
    )
    assert point == pytest.approx((500.0, 0.0))


def test_timetable_point_hop_after_a_dwell_counts_from_departure() -> None:
    """A dwell starts on arrival, and the next hop runs from departure."""
    stops = (
        DIAGONAL[0],
        PatternStop(
            arrives=EIGHT + 240,
            departs=EIGHT + 420,
            point=(300.0, 700.0),
        ),
        DIAGONAL[1],
    )
    trip = pattern(stops=stops)
    arriving = START + timedelta(minutes=4)
    later = START + timedelta(minutes=8, seconds=30)
    assert timetable_point(
        pattern=trip,
        shape=None,
        now=arriving,
        zone=ZONE,
    ) == (
        300.0,
        700.0,
    )
    assert timetable_point(
        pattern=trip,
        shape=None,
        now=later,
        zone=ZONE,
    ) == pytest.approx((650.0, 850.0))


def test_timetable_point_at_the_first_departure_is_the_first_stop() -> None:
    """At the first departure time the trip is at the first stop."""
    point = timetable_point(
        pattern=pattern(),
        shape=None,
        now=START,
        zone=ZONE,
    )
    assert point == (0.0, 0.0)


DWELL = (
    DIAGONAL[0],
    PatternStop(
        arrives=EIGHT + 240,
        departs=EIGHT + 420,
        point=(300.0, 700.0),
    ),
    DIAGONAL[1],
)


@pytest.mark.parametrize(
    ('seconds', 'hop'),
    [
        (EIGHT, (0, 0.0)),
        (EIGHT + 120, (0, 0.5)),
        (EIGHT + 300, (1, 0.0)),
        (EIGHT + 510, (1, 0.5)),
        (EIGHT + TEN_MINUTES, (1, 1.0)),
        (EIGHT - 1, None),
        (EIGHT + TEN_MINUTES + 1, None),
    ],
)
def test_hop_at_matches_point_at(
    seconds: int, hop: tuple[int, float] | None,
) -> None:
    """The hop and fraction give the same point `point_at` finds."""
    trip = pattern(stops=DWELL)
    assert hop_at(pattern=trip, seconds=seconds) == (
        None if hop is None else pytest.approx(hop)
    )
    if hop is not None:
        index, fraction = hop
        start, end = DWELL[index].point, DWELL[index + 1].point
        assert start is not None
        assert end is not None
        assert point_at(
            pattern=trip, shape=None, seconds=seconds,
        ) == pytest.approx((
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        ))


def test_hop_at_needs_two_stops() -> None:
    """A trip with one call has no hop; `point_at` puts it at the stop."""
    trip = pattern(stops=DWELL[:1])
    assert hop_at(pattern=trip, seconds=EIGHT) is None
    assert point_at(pattern=trip, shape=None, seconds=EIGHT) == (0.0, 0.0)
    assert point_at(
        pattern=pattern(stops=DWELL), shape=None, seconds=EIGHT - 1,
    ) is None


def test_hop_near_picks_nearest_hop() -> None:
    """A point beside the second hop projects onto it, ties to the first."""
    stops = (
        PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
        PatternStop(arrives=EIGHT, departs=EIGHT, point=(1000.0, 0.0)),
        PatternStop(arrives=EIGHT, departs=EIGHT, point=(1000.0, 1000.0)),
    )
    trip = pattern(stops=stops)
    assert hop_near(pattern=trip, point=(1010.0, 250.0)) == pytest.approx(
        (1, 0.25),
    )
    assert hop_near(pattern=trip, point=(1000.0, 0.0)) == pytest.approx(
        (0, 1.0),
    )


def test_hop_near_skips_hops_without_points() -> None:
    """A hop with a pointless stop is never chosen; none at all is None."""
    stops = (
        PatternStop(arrives=EIGHT, departs=EIGHT, point=None),
        PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
        PatternStop(arrives=EIGHT, departs=EIGHT, point=(1000.0, 0.0)),
    )
    assert hop_near(
        pattern=pattern(stops=stops), point=(-500.0, 0.0),
    ) == pytest.approx((1, 0.0))
    assert hop_near(
        pattern=pattern(stops=stops[:2]), point=(0.0, 0.0),
    ) is None
