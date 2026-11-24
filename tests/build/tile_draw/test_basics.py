"""Tests for tile draw: basics."""

import numpy as np

from paperroute_device.render.canvas import PAPER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.map_style import DARK, LIGHT
from paperroute_transit.build.tile_draw import draw_window, render_tile
from paperroute_transit.core.tiles import TILE
from paperroute_transit.render.tile_codec import (
    GREYS,
    diffuse,
    pack_bits,
    unpack_grey,
)
from tests.build.tile_draw.helpers import origin_window, tile_pixel


def grey_at(*, store: FeatureStore, lon: float, lat: float, mpp: int) -> int:
    """
    Draw the tile holding a point and read the point's grey.

    Parameters
    ----------
    store : FeatureStore
        The features.
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    mpp : int
        Metres per pixel.

    Returns
    -------
    int
        The grey level at the point.
    """
    window, (x, y) = tile_pixel(lon=lon, lat=lat, mpp=mpp)
    return int(draw_window(store=store, window=window)[y, x])


def test_tile_has_four_levels_only(store: FeatureStore) -> None:
    """Tile has four levels only."""
    window, _ = tile_pixel(lon=151.0, lat=-33.802, mpp=3)
    tile = draw_window(store=store, window=window)
    assert tile.shape == (TILE, TILE)
    assert tile.dtype == np.uint8
    assert set(np.unique(tile).tolist()) <= set(GREYS)


def test_primary_road_is_dark_at_3(store: FeatureStore) -> None:
    """Primary road is dark at 3."""
    assert grey_at(store=store, lon=151.003, lat=-33.802, mpp=3) == DARK


def test_minor_road_is_light_at_14(store: FeatureStore) -> None:
    """Minor road is light at 14."""
    assert grey_at(store=store, lon=151.003, lat=-33.8, mpp=14) == LIGHT


def test_park_is_light_and_sea_is_dark(store: FeatureStore) -> None:
    """Park is light and sea is dark."""
    assert grey_at(store=store, lon=150.997, lat=-33.795, mpp=3) == LIGHT
    assert grey_at(store=store, lon=151.011, lat=-33.8, mpp=3) == DARK


def test_render_tile_round_trips(store: FeatureStore) -> None:
    """Render tile round trips."""
    window, _ = tile_pixel(lon=151.0, lat=-33.802, mpp=3)
    grey, bits = render_tile(
        store=store,
        mpp=3,
        col=window.left // TILE,
        row=window.top // TILE,
        projection=SYDNEY_PROJECTION,
    )
    np.testing.assert_array_equal(
        unpack_grey(blob=grey),
        draw_window(store=store, window=window),
    )
    assert bits == pack_bits(
        paper=diffuse(grey=draw_window(store=store, window=window)),
    )


def test_empty_window_is_paper(empty: FeatureStore) -> None:
    """Empty window is paper."""
    window = origin_window(mpp=8)
    tile = draw_window(store=empty, window=window)
    assert (tile == PAPER).all()
