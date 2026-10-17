"""Tests for the tile grid."""

from paperroute.core.tiles import (
    GRID_ORIGIN,
    from_pixels,
    tile_of,
    tiles_covering,
    to_pixels,
)


def test_grid_origin_is_pixel_zero() -> None:
    """The grid origin is the top-left pixel."""
    assert to_pixels(point=GRID_ORIGIN, mpp=3) == (0.0, 0.0)


def test_pixels_round_trip() -> None:
    """Metres to pixels and back returns the same point."""
    point = (GRID_ORIGIN[0] + 900.0, GRID_ORIGIN[1] - 450.0)
    pixel = to_pixels(point=point, mpp=3)
    assert pixel == (300.0, 150.0)
    assert from_pixels(pixel=pixel, mpp=3) == point


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
