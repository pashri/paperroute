"""Tests for the map projection and shape geometry."""

import math

from paperroute_transit.core.geo import (
    Projection,
    make_shape,
    pack_shape,
    point_along,
    stop_distances,
    to_lonlat,
    to_metres,
    unpack_shape,
)
from tests.projection import PROJECTION

TOWN_HALL = (151.2070, -33.8732)
PARRAMATTA = (151.00533, -33.81749)
EARTH_RADIUS: float = 6_371_000.0  # metres


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
