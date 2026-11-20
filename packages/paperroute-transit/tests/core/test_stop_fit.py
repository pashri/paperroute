"""Tests for placing a trip's calls along its line."""

from typing import Final

import pytest

from paperroute_transit.core.geo import Shape, make_shape
from paperroute_transit.core.stop_fit import forward_distances, stop_distances

TINY: Final[Shape] = make_shape(
    shape_id='tiny',
    points=[
        (0.0, 0.0), (1e-170, 0.0), (100.0, 0.0), (100.0, 0.0),
        (200.0, 0.0),
    ],
)


def test_stop_distances_skips_zero_length_segments() -> None:
    """Repeated and underflowing vertices place calls without error."""
    assert stop_distances(
        shape=TINY, stops=[(50.0, 3.0), (150.0, 3.0)],
    ) == pytest.approx((50.0, 150.0))


def test_stop_distances_handles_no_stops_and_a_point_shape() -> None:
    """No stops give an empty result; a one-point shape places at zero."""
    point = make_shape(shape_id='point', points=[(5.0, 5.0)])
    assert stop_distances(shape=TINY, stops=[]) == ()
    assert stop_distances(shape=point, stops=[(1.0, 1.0)]) == (0.0,)
    assert forward_distances(shape=point, stops=[(1.0, 1.0)]) == (0.0,)


def test_stop_distances_never_decrease_on_a_loop() -> None:
    """A loop route passing a corner twice keeps its stop order."""
    shape = make_shape(
        shape_id='loop',
        points=[
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 100.0),
            (0.0, 100.0),
            (0.0, 5.0),
        ],
    )
    distances = stop_distances(
        shape=shape,
        stops=[(0.0, 0.0), (100.0, 50.0), (0.0, 2.0)],
    )
    assert distances == (0.0, 150.0, 395.0)
