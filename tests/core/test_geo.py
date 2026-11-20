"""Tests for the shape search in `core.geo`."""

import math
from collections.abc import Sequence

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from paperroute_transit.core.geo import (
    Point,
    Shape,
    make_shape,
    nearest_from,
    project_onto_segment,
)

CASES: int = 2000
COORDINATE = st.floats(min_value=-400.0, max_value=400.0).map(
    lambda value: round(value, 3),
)
WHOLE = st.integers(min_value=-40, max_value=40).map(float)
POINT = st.tuples(COORDINATE, COORDINATE) | st.tuples(WHOLE, WHOLE)


def brute_nearest_from(
    *, shape: Shape, stop: Point, first: int,
) -> tuple[int, float]:
    """The plain scan of every segment from `first`."""
    best = (math.inf, first, shape.along[first])
    for index in range(first, len(shape.points) - 1):
        gap, offset = project_onto_segment(
            point=stop,
            start=shape.points[index],
            end=shape.points[index + 1],
        )
        if gap < best[0]:
            best = (gap, index, shape.along[index] + offset)
    return best[1], best[2]


@st.composite
def tracks(draw: st.DrawFn) -> list[Point]:
    """A path that sometimes repeats a point or returns to one."""
    points: list[Point] = [draw(POINT)]
    for _ in range(draw(st.integers(min_value=0, max_value=40))):
        step = draw(st.sampled_from(('new', 'repeat', 'revisit')))
        if step == 'repeat':
            points.append(points[-1])
        elif step == 'revisit':
            points.append(draw(st.sampled_from(points)))
        else:
            points.append(draw(POINT))
    return points


@settings(
    max_examples=CASES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(points=tracks(), stop=POINT, offset=st.integers(min_value=0))
def test_nearest_from_matches_the_plain_scan(
    points: Sequence[Point], stop: Point, offset: int,
) -> None:
    """The search gives the plain scan's segment and distance."""
    shape = make_shape(shape_id='s', points=points)
    first = offset % len(points)
    assert nearest_from(shape=shape, stop=stop, first=first) == (
        brute_nearest_from(shape=shape, stop=stop, first=first)
    )
