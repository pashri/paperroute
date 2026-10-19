"""Tests for the stop positions kept between pattern loads."""

from datetime import date
from typing import Final

import pytest

from paperroute.core.geo import Point, Shape, make_shape
from paperroute.core.model import Mode
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute.measures import StopMeasures

LINE: Final[Shape] = make_shape(
    shape_id='s1', points=[(0.0, 0.0), (300.0, 0.0)],
)
OTHER: Final[Shape] = make_shape(
    shape_id='s2', points=[(0.0, 0.0), (0.0, 300.0)],
)


def trip(*, trip_id: str, points: tuple[Point | None, ...],
         shape_id: str = 's1') -> TripPattern:
    """
    Make a trip calling at some points.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    points : tuple[Point | None, ...]
        Stop points in order.
    shape_id : str
        Its shape.

    Returns
    -------
    TripPattern
        The trip, not yet measured.
    """
    return TripPattern(
        trip_id=trip_id,
        route='T1',
        mode=Mode.TRAIN,
        shape_id=shape_id,
        stops=tuple(
            PatternStop(arrives=60 * i, departs=60 * i, point=point)
            for i, point in enumerate(points)
        ),
        days=(date(2026, 10, 19),),
    )


STOPS: Final[tuple[Point, ...]] = ((0.0, 0.0), (100.0, 0.0), (300.0, 0.0))


def test_trips_on_one_shape_share_one_measurement() -> None:
    """A second trip with the same shape and stops reuses the first's."""
    measures = StopMeasures()
    first = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    second = measures.measure(pattern=trip(trip_id='b', points=STOPS),
                              shape=LINE)
    assert first.along == pytest.approx((0.0, 100.0, 300.0))
    assert second.trip_id == 'b'
    assert second.along is first.along


def test_other_stops_are_measured_apart() -> None:
    """A trip calling at other stops on the shape gets its own places."""
    measures = StopMeasures()
    measures.measure(pattern=trip(trip_id='a', points=STOPS), shape=LINE)
    short = measures.measure(
        pattern=trip(trip_id='b', points=((0.0, 0.0), (200.0, 0.0))),
        shape=LINE,
    )
    assert short.along == pytest.approx((0.0, 200.0))


def test_rebuilt_shape_is_measured_again() -> None:
    """A shape of the same ID but another length is not reused."""
    measures = StopMeasures()
    first = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    longer = make_shape(shape_id='s1', points=[(0.0, 0.0), (400.0, 0.0)])
    again = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=longer)
    assert again.along is not first.along


def test_least_used_shape_goes_with_its_measurements() -> None:
    """Past the shape limit the least recently used shape is dropped."""
    measures = StopMeasures(max_shapes=1)
    first = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    measures.measure(
        pattern=trip(trip_id='b', points=((0.0, 0.0),), shape_id='s2'),
        shape=OTHER,
    )
    again = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    assert again.along is not first.along
    assert again.along == pytest.approx(first.along)


def test_stop_lists_per_shape_are_bounded() -> None:
    """Past the per-shape limit the oldest stop list is dropped."""
    measures = StopMeasures(max_stop_lists=1)
    first = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    measures.measure(
        pattern=trip(trip_id='b', points=((0.0, 0.0),)), shape=LINE,
    )
    again = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    assert again.along is not first.along


def test_used_stop_list_outlasts_an_older_unused_one() -> None:
    """A hit keeps a stop list; the least recently used one goes."""
    measures = StopMeasures(max_stop_lists=2)
    short = ((0.0, 0.0), (200.0, 0.0))
    first = measures.measure(pattern=trip(trip_id='a', points=STOPS),
                             shape=LINE)
    other = measures.measure(pattern=trip(trip_id='b', points=short),
                             shape=LINE)
    measures.measure(pattern=trip(trip_id='c', points=STOPS), shape=LINE)
    measures.measure(
        pattern=trip(trip_id='d', points=((0.0, 0.0),)), shape=LINE,
    )
    kept = measures.measure(pattern=trip(trip_id='e', points=STOPS),
                            shape=LINE)
    dropped = measures.measure(pattern=trip(trip_id='f', points=short),
                               shape=LINE)
    assert kept.along is first.along
    assert dropped.along is not other.along


def test_unmeasurable_trips_pass_through() -> None:
    """No shape, or a stop with no point, leaves the trip unmeasured."""
    measures = StopMeasures()
    plain = trip(trip_id='a', points=STOPS)
    assert measures.measure(pattern=plain, shape=None) is plain
    gap = trip(trip_id='b', points=((0.0, 0.0), None))
    assert measures.measure(pattern=gap, shape=LINE).along is None
