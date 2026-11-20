"""Tests for the map projection and shape geometry."""

import math
from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute_transit.core.geo import (
    Point,
    Projection,
    Shape,
    cut,
    make_shape,
    nearest_along,
    nearest_from,
    pack_shape,
    point_along,
    project_onto_segment,
    to_lonlat,
    to_metres,
    unpack_shape,
)
from tests.projection import PROJECTION

TOWN_HALL = (151.2070, -33.8732)
PARRAMATTA = (151.00533, -33.81749)
EARTH_RADIUS: float = 6_371_000.0  # metres
SHORT = make_shape(shape_id='s', points=((0, 0), (100, 0), (200, 0)))


def haversine(*, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return the great-circle distance in metres between two lon/lat."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(h))


def test_to_metres_origin_is_zero() -> None:
    """The projection origin maps to (0, 0)."""
    assert to_metres(
        lon=151.2093,
        lat=-33.8688,
        projection=PROJECTION,
    ) == (0.0, 0.0)


def test_round_trip_lonlat() -> None:
    """Projecting and unprojecting returns the same degrees."""
    point = to_metres(
        lon=PARRAMATTA[0],
        lat=PARRAMATTA[1],
        projection=PROJECTION,
    )
    lon, lat = to_lonlat(point=point, projection=PROJECTION)
    assert abs(lon - PARRAMATTA[0]) < 1e-9
    assert abs(lat - PARRAMATTA[1]) < 1e-9


def test_round_trip_with_projection() -> None:
    """Another projection's origin is metre zero and round-trips."""
    london = Projection(
        origin_lon=0.0, origin_lat=51.5, grid_lon=-1.0, grid_lat=52.0,
    )
    point = to_metres(lon=151.0, lat=-33.5, projection=london)
    lon, lat = to_lonlat(point=point, projection=london)
    assert abs(lon - 151.0) < 1e-9
    assert abs(lat - -33.5) < 1e-9
    assert to_metres(lon=0.0, lat=51.5, projection=london) == (0.0, 0.0)


def test_to_metres_matches_great_circle() -> None:
    """Town Hall to Parramatta is within 1% of the haversine."""
    a = to_metres(lon=TOWN_HALL[0], lat=TOWN_HALL[1], projection=PROJECTION)
    b = to_metres(lon=PARRAMATTA[0], lat=PARRAMATTA[1], projection=PROJECTION)
    flat = math.hypot(b[0] - a[0], b[1] - a[1])
    great = haversine(a=TOWN_HALL, b=PARRAMATTA)
    assert abs(flat - great) / great < 0.01
    assert b[0] < a[0]
    assert b[1] > a[1]


def test_point_along_interpolates_and_clamps() -> None:
    """Distances inside a shape interpolate and outside clamp."""
    shape = make_shape(
        shape_id='L',
        points=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
    )
    assert shape.along == (0.0, 10.0, 20.0)
    assert point_along(shape=shape, distance=15.0) == (10.0, 5.0)
    assert point_along(shape=shape, distance=-1.0) == (0.0, 0.0)
    assert point_along(shape=shape, distance=99.0) == (10.0, 10.0)


def test_pack_shape_round_trips_to_a_decimetre() -> None:
    """Packing keeps every coordinate to within a decimetre."""
    shape = make_shape(
        shape_id='s',
        points=[(-1234.56, 789.01), (-1000.04, 800.5), (5.55, -7.77)],
    )
    back = unpack_shape(shape_id='s', blob=pack_shape(shape=shape))
    assert back.shape_id == 's'
    for want, got in zip(shape.points, back.points, strict=True):
        assert math.hypot(want[0] - got[0], want[1] - got[1]) <= 0.1
    for want_m, got_m in zip(shape.along, back.along, strict=True):
        assert abs(want_m - got_m) <= 0.1


def test_cut_takes_a_stretch_of_a_shape() -> None:
    """A cut starts and ends at the asked distances."""
    assert cut(shape=SHORT, start=50.0, end=150.0) == (
        (50.0, 0.0), (100, 0), (150.0, 0.0),
    )


def test_cut_clamps_to_the_shape() -> None:
    """Beyond the shape a cut is empty; a long cut stops at the end."""
    assert cut(shape=SHORT, start=250.0, end=300.0) == ()
    assert cut(shape=SHORT, start=150.0, end=150.0) == ()
    assert cut(shape=SHORT, start=150.0, end=900.0)[-1] == (200.0, 0.0)


def test_nearest_along_projects_onto_the_shape() -> None:
    """A point beside the shape is measured where it projects."""
    assert nearest_along(shape=SHORT, point=(130.0, 7.0)) == 130.0


CASES: int = 2000
COORDINATE = st.floats(min_value=-400.0, max_value=400.0).map(
    lambda value: round(value, 3),
)
WHOLE = st.integers(min_value=-40, max_value=40).map(float)
POINT = st.tuples(COORDINATE, COORDINATE) | st.tuples(WHOLE, WHOLE)
SPINE = st.tuples(
    st.just(0.0),
    st.floats(min_value=0.0, max_value=30.0).map(
        lambda value: round(value, 2),
    ),
)
BESIDE = st.tuples(
    st.floats(min_value=-1.0, max_value=1.0).map(
        lambda value: round(value, 2),
    ),
    st.floats(min_value=-1.0, max_value=2.0).map(
        lambda value: round(value, 2),
    ),
)


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
def tracks(
    draw: st.DrawFn, *, spot: st.SearchStrategy[Point] = POINT,
) -> list[Point]:
    """A path that sometimes repeats a point or returns to one."""
    points: list[Point] = [draw(spot)]
    for _ in range(draw(st.integers(min_value=0, max_value=40))):
        step = draw(st.sampled_from(('new', 'repeat', 'revisit')))
        if step == 'repeat':
            points.append(points[-1])
        elif step == 'revisit':
            points.append(draw(st.sampled_from(points)))
        else:
            points.append(draw(spot))
    return points


def stored(*, shape: Shape) -> Shape:
    """The shape as the timetable keeps it, in rounded decimetres."""
    return unpack_shape(shape_id=shape.shape_id, blob=pack_shape(shape=shape))


def same_search(*, shape: Shape, stop: Point, first: int) -> bool:
    """Whether the search and the plain scan agree."""
    return nearest_from(shape=shape, stop=stop, first=first) == (
        brute_nearest_from(shape=shape, stop=stop, first=first)
    )


@settings(max_examples=CASES, deadline=None, derandomize=True)
@given(points=tracks(), stop=POINT, offset=st.integers(min_value=0))
def test_nearest_from_matches_the_plain_scan(
    points: Sequence[Point], stop: Point, offset: int,
) -> None:
    """The search gives the plain scan's segment and distance."""
    shape = make_shape(shape_id='s', points=points)
    assert same_search(shape=shape, stop=stop, first=offset % len(points))


@settings(max_examples=CASES, deadline=None, derandomize=True)
@given(
    points=tracks(spot=SPINE),
    stop=BESIDE,
    offset=st.integers(min_value=0),
)
def test_nearest_from_matches_the_plain_scan_on_stored_shapes(
    points: Sequence[Point], stop: Point, offset: int,
) -> None:
    """The same holds once packing has rounded points and distances."""
    shape = stored(shape=make_shape(shape_id='s', points=points))
    assert same_search(shape=shape, stop=stop, first=offset % len(points))


def test_nearest_from_finds_a_pass_that_rounding_shortened() -> None:
    """A stretch stored shorter than its straight line is not skipped."""
    shape = Shape(
        shape_id='s',
        points=((0.0, 1.0), (0.0, 20.0), (0.0, 0.95)),
        along=(0.0, 19.0, 37.95),
    )
    assert nearest_from(shape=shape, stop=(0.0, 0.0), first=0) == (1, 38.05)


def test_project_onto_segment_skips_an_underflowing_segment() -> None:
    """A segment whose squared length underflows measures from its start."""
    assert project_onto_segment(
        point=(1.0, 1.0), start=(0.0, 0.0), end=(1e-170, 0.0),
    ) == pytest.approx((math.sqrt(2.0), 0.0))
