"""Tests for the tile grid."""

import pytest

from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.tiles import (
    ZOOMS,
    from_pixels,
    metres_of,
    tile_of,
    tiles_covering,
    to_pixels,
)
from tests.projection import PROJECTION


def test_grid_origin_is_pixel_zero() -> None:
    """The grid origin is the top-left pixel."""
    assert to_pixels(
        point=PROJECTION.grid_origin,
        mpp=3,
        projection=PROJECTION,
    ) == (0.0, 0.0)


def test_pixels_round_trip() -> None:
    """Metres to pixels and back returns the same point."""
    grid = PROJECTION.grid_origin
    point = (grid[0] + 900.0, grid[1] - 450.0)
    pixel = to_pixels(point=point, mpp=3, projection=PROJECTION)
    assert pixel == (300.0, 150.0)
    assert from_pixels(pixel=pixel, mpp=3, projection=PROJECTION) == point


def test_metres_of_flips_rows_to_north() -> None:
    """A pixel box's top edge is its north edge in metres."""
    grid = PROJECTION.grid_origin
    box = metres_of(box=(0.0, 0.0, 10.0, 20.0), mpp=3, projection=PROJECTION)
    assert box == (grid[0], grid[1] - 60.0, grid[0] + 30.0, grid[1])


def test_tile_of_floors() -> None:
    """Tile indices come from floor division."""
    assert tile_of(pixel=(255.9, 256.0)) == (0, 1)


def test_tiles_covering_spans_edges() -> None:
    """A box crossing a tile edge lists both tiles, row-major."""
    tiles = tiles_covering(left=250, right=260, top=0, bottom=10)
    assert tiles == [(0, 0), (1, 0)]


def test_tiles_covering_is_half_open() -> None:
    """A box ending exactly on a tile edge excludes the next tile."""
    tiles = tiles_covering(left=0, right=256, top=0, bottom=256)
    assert tiles == [(0, 0)]


@pytest.mark.parametrize('mpp', ZOOMS)
def test_pixels_round_trip_west_and_north_of_the_origin(mpp: int) -> None:
    """Broken Hill and Tweed Heads have negative columns or rows."""
    broken_hill = to_metres(lon=141.45, lat=-31.95, projection=PROJECTION)
    tweed_heads = to_metres(lon=153.54, lat=-28.18, projection=PROJECTION)
    for point in (broken_hill, tweed_heads):
        pixel = to_pixels(point=point, mpp=mpp, projection=PROJECTION)
        assert from_pixels(
            pixel=pixel,
            mpp=mpp,
            projection=PROJECTION,
        ) == pytest.approx(point)
    assert to_pixels(point=broken_hill, mpp=mpp, projection=PROJECTION)[0] < 0
    assert to_pixels(point=tweed_heads, mpp=mpp, projection=PROJECTION)[1] < 0
    assert tile_of(pixel=to_pixels(
        point=broken_hill,
        mpp=mpp,
        projection=PROJECTION,
    ))[0] < 0
    assert tile_of(pixel=to_pixels(
        point=tweed_heads,
        mpp=mpp,
        projection=PROJECTION,
    ))[1] < 0
