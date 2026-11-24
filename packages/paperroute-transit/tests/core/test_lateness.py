"""Tests for placing a position on its trip and reading its lateness."""

from datetime import timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    TEN_MINUTES,
    pattern,
    vehicle,
)
from paperroute_transit.core.geo import Point, make_shape
from paperroute_transit.core.lateness import (
    Line,
    Placed,
    line_of,
    placed,
    timetable_at,
)
from paperroute_transit.core.patterns import PatternStop, TripPattern, measure

ZONE: Final[ZoneInfo] = city_under_test().zone
EAST: Final[TripPattern] = pattern()  # (0, 0) at 8:00 to (1000, 0) at 8:10
CORNER = make_shape(
    shape_id='L', points=[(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)],
)
DIAGONAL = (
    PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
    PatternStop(
        arrives=EIGHT + TEN_MINUTES,
        departs=EIGHT + TEN_MINUTES,
        point=(1000.0, 1000.0),
    ),
)
U_TURN = make_shape(
    shape_id='U',
    points=[(0.0, 0.0), (1000.0, 0.0), (1000.0, 20.0), (0.0, 20.0)],
)
OUT_AND_BACK = (
    PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
    PatternStop(arrives=EIGHT + 600, departs=EIGHT + 600, point=(1000.0, 0.0)),
    PatternStop(arrives=EIGHT + 1200, departs=EIGHT + 1200, point=(0.0, 20.0)),
)


def at(seconds: int) -> int:
    """Give the Unix time some seconds after 8:00 on the test day."""
    return int((START + timedelta(seconds=seconds)).timestamp())


def place_on(
    trip: TripPattern, line: Line, *, point: Point, seconds: int,
    bearing: float | None = None,
) -> Placed | None:
    """Place a vehicle at a point some seconds after 8:00."""
    found = vehicle(timestamp=at(seconds), point=point, bearing=bearing)
    return placed(
        pattern=trip, line=line, vehicle=found, day=START.date(), zone=ZONE,
    )


def straight() -> Line:
    """The default pattern's straight line."""
    line = line_of(pattern=EAST, shape=None)
    assert line is not None
    return line


def test_line_of_uses_the_measured_shape() -> None:
    """A measured trip is placed on its own shape."""
    trip = measure(pattern=pattern(shape_id='L', stops=DIAGONAL), shape=CORNER)
    line = line_of(pattern=trip, shape=CORNER)
    assert line is not None and line.shape is CORNER
    assert line.stops == pytest.approx((0.0, 2000.0))


def test_line_of_runs_straight_without_a_shape() -> None:
    """A trip without a shape runs straight from stop to stop."""
    assert straight().stops == pytest.approx((0.0, 1000.0))


def test_line_of_needs_every_stop_placed() -> None:
    """A stop with no point leaves the trip without a line."""
    stops = (
        DIAGONAL[0],
        PatternStop(arrives=EIGHT, departs=EIGHT, point=None),
    )
    assert line_of(pattern=pattern(stops=stops), shape=None) is None


@pytest.mark.parametrize(
    ('along', 'seconds'),
    [(-5.0, EIGHT), (0.0, EIGHT), (500.0, EIGHT + 300), (2000.0, EIGHT + 600)],
)
def test_timetable_at_reads_between_the_stops(
    along: float, seconds: int,
) -> None:
    """The timetable time runs evenly between one stop and the next."""
    found = timetable_at(pattern=EAST, line=straight(), along=along)
    assert found == pytest.approx(seconds)


@pytest.mark.parametrize(
    ('seconds', 'late'),
    [(420, 120), (300, 0), (180, -120)],
)
def test_placed_reads_late_on_time_and_early(seconds: int, late: int) -> None:
    """Halfway along at 8:07, 8:05 and 8:03 is late, on time and early."""
    found = place_on(EAST, straight(), point=(500.0, 0.0), seconds=seconds)
    assert found is not None
    assert (found.along, found.late, found.ends) == (500.0, late, 500.0)


def test_placed_leaves_a_position_off_the_line() -> None:
    """A position more than 100 m from the line is not placed."""
    line = straight()
    assert place_on(EAST, line, point=(500.0, 100.0), seconds=300)
    assert place_on(EAST, line, point=(500.0, 101.0), seconds=300) is None


def test_placed_leaves_a_bus_waiting_at_its_first_stop() -> None:
    """
    Near the first stop before 8:00 there is no estimate; after, there is.
    """
    line = straight()
    assert place_on(EAST, line, point=(20.0, 0.0), seconds=-120) is None
    found = place_on(EAST, line, point=(20.0, 0.0), seconds=60)
    assert found is not None and found.late == 48


def test_placed_leaves_a_bus_at_its_last_stop() -> None:
    """Within 30 m of the last stop the trip has finished."""
    assert place_on(EAST, straight(), point=(975.0, 0.0), seconds=900) is None


def test_placed_leaves_a_one_point_shape_without_a_place() -> None:
    """A shape of one point gives no place rather than an error."""
    shape = make_shape(shape_id='S', points=[(0.0, 0.0)])
    stub = Line(shape=shape, stops=(0.0, 1000.0))
    assert place_on(EAST, stub, point=(500.0, 0.0), seconds=300) is None


def test_placed_follows_the_shape_not_the_straight_hop() -> None:
    """A bus on the corner is on its shape, and far from the straight hop."""
    trip = measure(pattern=pattern(shape_id='L', stops=DIAGONAL), shape=CORNER)
    shaped = line_of(pattern=trip, shape=CORNER)
    bare = line_of(pattern=pattern(stops=DIAGONAL), shape=None)
    assert shaped is not None and bare is not None
    found = place_on(trip, shaped, point=(1000.0, 500.0), seconds=570)
    assert found is not None
    assert (found.along, found.late, found.ends) == (1500.0, 120, 500.0)
    assert place_on(
        pattern(stops=DIAGONAL), bare, point=(1000.0, 500.0), seconds=570,
    ) is None


@pytest.mark.parametrize(
    ('bearing', 'along', 'late'),
    [(270.0, 1520.0, 24), (90.0, 500.0, 630), (None, 1520.0, 24)],
)
def test_placed_takes_the_stretch_the_bus_is_heading_along(
    bearing: float | None, along: float, late: int,
) -> None:
    """Between the two legs of a U-turn, heading decides, then the time."""
    trip = measure(
        pattern=pattern(shape_id='U', stops=OUT_AND_BACK), shape=U_TURN,
    )
    line = line_of(pattern=trip, shape=U_TURN)
    assert line is not None
    found = place_on(
        trip, line, point=(500.0, 10.0), seconds=930, bearing=bearing,
    )
    assert found is not None
    assert found.along == pytest.approx(along) and found.late == late
