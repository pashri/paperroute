"""Tests for placing a trip's calls along its line."""

import math
from collections.abc import Sequence
from typing import Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute_transit.core.geo import Point, Shape, make_shape
from paperroute_transit.core.stop_fit import (
    CANDIDATES,
    Fit,
    forward_distances,
    kept,
    passes,
    stop_distances,
    total_miss,
)
from tests.core.test_geo import CASES, POINT, tracks

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


OUT_AND_BACK: Final[Shape] = make_shape(
    shape_id='out-and-back',
    points=[(0.0, 0.0), (1000.0, 0.0), (1000.0, 10.0), (0.0, 10.0)],
)
CIRCLE: Final[Shape] = make_shape(
    shape_id='circle',
    points=[
        (0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0),
        (0.0, -3.0),
    ],
)
CENTRAL: Final[Point] = (-4.0, -1.0)
TERMINAL_LOOP: Final[Shape] = make_shape(
    shape_id='terminal-loop',
    points=[
        (0.0, 0.0), (500.0, 0.0), (500.0, -400.0), (800.0, -400.0),
        (800.0, 10.0), (0.0, 10.0),
    ],
)
STRAIGHT: Final[Shape] = make_shape(
    shape_id='straight', points=[(0.0, 0.0), (1000.0, 0.0)],
)
ZIGZAG: Final[Shape] = make_shape(
    shape_id='zigzag',
    points=[(-100.0 if k % 2 == 0 else 100.0, 0.0) for k in range(41)],
)


def test_passes_keeps_the_nearest_up_to_the_cap() -> None:
    """A line through a stop forty times keeps the nearest passes."""
    found = passes(shape=ZIGZAG, stop=(0.0, 0.0), ceiling=math.inf)
    assert [one.along for one in found] == [
        200.0 * k + 100.0 for k in range(CANDIDATES)
    ]


def test_kept_drops_fits_behind_a_cheaper_one_or_far_above_the_best() -> None:
    """A fit survives only when cheaper than all before it, near the best."""
    fits = [
        Fit(along=10.0, cost=100.0, before=None),
        Fit(along=0.0, cost=900.0, before=None),
        Fit(along=6.0, cost=350.0, before=None),
        Fit(along=5.0, cost=300.0, before=None),
    ]
    assert [(fit.along, fit.cost) for fit in kept(fits=fits)] == [
        (5.0, 300.0), (10.0, 100.0),
    ]


def test_stop_distances_keeps_the_way_out_on_an_out_and_back() -> None:
    """A call a decimetre nearer the way back stays on the way out."""
    stops = [
        (100.0, 2.0), (300.0, 5.05), (500.0, 2.0), (800.0, 2.0),
        (600.0, 12.0), (200.0, 12.0),
    ]
    assert stop_distances(shape=OUT_AND_BACK, stops=stops) == pytest.approx(
        (100.0, 300.0, 500.0, 800.0, 1410.0, 1810.0),
    )


def test_stop_distances_circle_starts_at_zero_and_ends_at_the_end() -> None:
    """A circle from one station back to it places both calls at its ends."""
    stops = [
        CENTRAL, (500.0, -5.0), (1005.0, 500.0), (500.0, 1005.0),
        (-5.0, 500.0), CENTRAL,
    ]
    assert stop_distances(shape=CIRCLE, stops=stops) == pytest.approx(
        (0.0, 500.0, 1500.0, 2500.0, 3500.0, 4001.0),
    )


def test_stop_distances_places_opposite_stops_across_a_short_loop() -> None:
    """Each of a pair across the road takes the pass of its own direction."""
    stops = [
        (100.0, -5.0), (300.0, 5.05), (505.0, -200.0), (650.0, -405.0),
        (805.0, -100.0), (300.0, 4.95), (50.0, 15.0),
    ]
    assert stop_distances(shape=TERMINAL_LOOP, stops=stops) == pytest.approx(
        (100.0, 300.0, 700.0, 1050.0, 1500.0, 2110.0, 2360.0),
    )


def test_stop_distances_repeated_calls_share_a_distance() -> None:
    """Two calls in a row at one stop get one distance."""
    stops = [
        (100.0, 2.0), (300.0, 5.05), (300.0, 5.05), (500.0, 2.0),
        (800.0, 2.0), (600.0, 12.0), (200.0, 12.0),
    ]
    assert stop_distances(shape=OUT_AND_BACK, stops=stops) == pytest.approx(
        (100.0, 300.0, 300.0, 500.0, 800.0, 1410.0, 1810.0),
    )


def test_stop_distances_holds_a_call_that_projects_behind() -> None:
    """A stop behind the call before waits there; the rest keep their pass."""
    stops = [
        (100.0, 2.0), (300.0, 5.05), (270.0, 2.0), (500.0, 2.0),
        (600.0, 12.0),
    ]
    assert stop_distances(shape=OUT_AND_BACK, stops=stops) == pytest.approx(
        (100.0, 300.0, 300.0, 500.0, 1410.0),
    )


def test_stop_distances_keeps_the_nearest_pass_at_a_free_end() -> None:
    """A last call nothing follows keeps the nearest pass, as today."""
    stops = [(100.0, 2.0), (300.0, 2.0), (500.0, 5.05)]
    assert stop_distances(shape=OUT_AND_BACK, stops=stops) == pytest.approx(
        (100.0, 300.0, 1510.0),
    )


def test_stop_distances_keeps_the_earlier_pass_on_an_exact_tie() -> None:
    """A last call as near the way back as the way out stays on the way out."""
    stops = [(100.0, 2.0), (300.0, 2.0), (500.0, 5.0)]
    assert stop_distances(shape=OUT_AND_BACK, stops=stops) == pytest.approx(
        (100.0, 300.0, 500.0),
    )


def test_stop_distances_ignores_a_fit_that_saves_less_than_the_tie() -> None:
    """A fit that saves under a decimetre of miss is not taken."""
    stops = [(100.0, 5.04), (100.0, 4.93)]
    assert stop_distances(shape=OUT_AND_BACK, stops=stops) == pytest.approx(
        (1910.0, 1910.0),
    )


def test_stop_distances_keeps_a_reversed_pair_at_the_first() -> None:
    """Two stops the wrong way round both sit at the first one's place."""
    assert stop_distances(
        shape=STRAIGHT, stops=[(700.0, 5.0), (200.0, 5.0)],
    ) == pytest.approx((700.0, 700.0))


@settings(max_examples=CASES, deadline=None, derandomize=True)
@given(points=tracks(), stops=st.lists(POINT, max_size=12))
def test_stop_distances_never_decrease_and_miss_no_more_than_today(
    points: Sequence[Point], stops: list[Point],
) -> None:
    """Distances never fall and miss the stops no more than before."""
    shape = make_shape(shape_id='s', points=points)
    placed = stop_distances(shape=shape, stops=stops)
    assert len(placed) == len(stops)
    assert all(b >= a for a, b in zip(placed, placed[1:]))
    if len(shape.points) > 1:
        today = forward_distances(shape=shape, stops=stops)
        assert total_miss(
            shape=shape, stops=stops, distances=placed,
        ) <= total_miss(shape=shape, stops=stops, distances=today) + 1e-9


HAIRPIN: Final[Shape] = make_shape(
    shape_id='hairpin',
    points=[(0.0, 0.0), (1000.0, 0.0), (0.0, 8.0), (0.0, 500.0)],
)
SPUR: Final[Shape] = make_shape(
    shape_id='spur',
    points=[(0.0, 0.0), (300.0, 0.0), (0.0, 6.0), (0.0, 500.0)],
)
TAIL: Final[Shape] = make_shape(
    shape_id='tail',
    points=[(0.0, 500.0), (0.0, 6.0), (300.0, 0.0), (0.0, 0.0)],
)
PACKED_CORNER: Final[Shape] = Shape(
    shape_id='packed-corner',
    points=((0.0, 0.0), (100.0, 0.0), (100.0, 100.0)),
    along=(0.0, 100.05, 200.05),
)


def test_passes_offers_both_legs_of_a_hairpin() -> None:
    """A stop a metre nearer the way out still has a pass on the way back."""
    found = passes(shape=HAIRPIN, stop=(500.0, 1.5), ceiling=math.inf)
    assert [one.along for one in found] == pytest.approx(
        [500.0, 1500.0], abs=0.01,
    )
    assert [one.gap for one in found] == pytest.approx([1.5, 2.5], abs=0.01)


def test_stop_distances_serves_a_hairpin_stop_on_the_way_back() -> None:
    """A call beside both legs after the tip lands on the way back."""
    stops = [
        (200.0, -2.0), (1003.0, 2.0), (500.0, 1.5), (100.0, 4.0),
        (0.0, 300.0),
    ]
    assert stop_distances(shape=HAIRPIN, stops=stops) == pytest.approx(
        (200.0, 1000.0, 1500.0, 1900.0, 2292.0), abs=0.1,
    )


def test_passes_keeps_the_first_vertex_of_a_spur() -> None:
    """A line's first point is a pass when the line then moves away."""
    found = passes(shape=SPUR, stop=(0.0, 3.2), ceiling=math.inf)
    assert [one.along for one in found] == pytest.approx(
        [0.0, 600.0], abs=0.01,
    )


def test_stop_distances_starts_a_spur_at_its_first_call() -> None:
    """A trip that starts with an out-and-back keeps its first call there."""
    stops = [(0.0, 3.2), (150.0, -1.0), (0.0, 400.0)]
    assert stop_distances(shape=SPUR, stops=stops) == pytest.approx(
        (0.0, 150.0, 994.06), abs=0.01,
    )


def test_passes_keeps_the_last_vertex_of_a_spur() -> None:
    """A line's last point is a pass when the line came to it from afar."""
    found = passes(shape=TAIL, stop=(0.0, 3.2), ceiling=math.inf)
    assert [one.along for one in found] == pytest.approx(
        [494.06, 1094.06], abs=0.01,
    )


def test_passes_skips_a_corner_the_next_segment_comes_nearer() -> None:
    """A segment's end vertex is no pass when the next segment is nearer."""
    found = passes(shape=PACKED_CORNER, stop=(110.0, 50.0), ceiling=math.inf)
    assert [one.along for one in found] == pytest.approx([150.05])
