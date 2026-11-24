"""Tests for tile draw: layers."""

import numpy as np
import pytest

from paperroute_device.render.canvas import INK, PAPER
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.map_style import DARK, LIGHT
from paperroute_transit.build.tile_draw import big_enough, draw_window
from tests.build.tile_draw.helpers import (
    SEAM_MPP,
    add_pixel_area,
    add_pixel_line,
    tile_window,
)


def test_paths_and_service_roads_lie_under_roads(
    empty: FeatureStore,
) -> None:
    """A dotted path and a light service road pass under a road."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    for layer, pixels in (
        ('path', [(x + 20, y + 50.5), (x + 200, y + 50.5)]),
        ('service', [(x + 20, y + 150.5), (x + 200, y + 150.5)]),
        ('road_primary', [(x + 100.5, y + 10), (x + 100.5, y + 240)]),
    ):
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=SEAM_MPP)
    tile = draw_window(store=empty, window=window)
    assert (tile[50, 20:90] == INK).any()
    assert (tile[50, 20:90] == PAPER).any()
    assert (tile[150, 20:90] == LIGHT).all()
    assert tile[50, 100] == DARK
    assert tile[150, 100] == DARK


def test_runway_is_paper_over_a_park_and_under_roads(
    empty: FeatureStore,
) -> None:
    """At 14 m/px a runway clears a park to paper and a road crosses it."""
    window = tile_window(mpp=14)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=empty,
        layer='park',
        box=(x + 20, y + 20, x + 220, y + 220),
        mpp=14,
    )
    for layer, pixels in (
        ('runway', [(x + 40, y + 120.5), (x + 200, y + 120.5)]),
        ('road_primary', [(x + 100.5, y + 30), (x + 100.5, y + 210)]),
    ):
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=14)
    tile = draw_window(store=empty, window=window)
    assert tile[60, 60] == LIGHT
    assert (tile[119:122, 60] == PAPER).all()
    assert tile[120, 100] == DARK


def test_water_is_drawn_over_parks(empty: FeatureStore) -> None:
    """Water is drawn over parks."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    for layer, box in (
        ('water', (x + 50, y + 50, x + 150, y + 150)),
        ('park', (x + 100, y + 100, x + 200, y + 200)),
    ):
        add_pixel_area(store=empty, layer=layer, box=box, mpp=SEAM_MPP)
    tile = draw_window(store=empty, window=window)
    assert tile[75, 75] == DARK
    assert tile[125, 125] == DARK
    assert tile[175, 175] == LIGHT


@pytest.mark.parametrize(
    ('side', 'expected'),
    [(3.0, False), (3.1, True)],
)
def test_big_enough_needs_more_than_three_pixels_squared(
    side: float,
    expected: bool,
) -> None:
    """Big enough needs more than three pixels squared."""
    ring = np.array([(0, 0), (side * 8, 0), (side * 8, 24), (0, 24), (0, 0)])
    assert big_enough(rings=[ring], mpp=8) is expected


def test_small_area_is_not_filled(empty: FeatureStore) -> None:
    """Small area is not filled."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=empty,
        layer='park',
        box=(x + 10, y + 10, x + 12.9, y + 13),
        mpp=SEAM_MPP,
    )
    assert (draw_window(store=empty, window=window) == PAPER).all()


@pytest.mark.parametrize(('mpp', 'overlap'), [(14, PAPER), (8, LIGHT)])
def test_ground_fills_stack_in_order(
    empty: FeatureStore,
    mpp: int,
    overlap: int,
) -> None:
    """Water covers an apron, which covers green from 14 m/px out."""
    window = tile_window(mpp=mpp)
    x, y = float(window.left), float(window.top)
    for layer, box in (
        ('water', (x + 100, y + 100, x + 200, y + 200)),
        ('apron', (x + 60, y + 60, x + 160, y + 160)),
        ('green', (x + 20, y + 20, x + 120, y + 120)),
    ):
        add_pixel_area(store=empty, layer=layer, box=box, mpp=mpp)
    tile = draw_window(store=empty, window=window)
    assert tile[40, 40] == LIGHT
    assert tile[80, 80] == overlap
    assert tile[110, 110] == DARK


@pytest.mark.parametrize(('mpp', 'grey'), [(3, DARK), (5, DARK), (8, PAPER)])
def test_platform_areas_fill_at_3_and_5_only(
    empty: FeatureStore,
    mpp: int,
    grey: int,
) -> None:
    """A platform area is dark grey at 3 and 5 m/px and absent beyond."""
    window = tile_window(mpp=mpp)
    x, y = float(window.left), float(window.top)
    add_pixel_area(
        store=empty,
        layer='platform',
        box=(x + 50, y + 50, x + 150, y + 70),
        mpp=mpp,
    )
    assert draw_window(store=empty, window=window)[60, 100] == grey


def test_platform_way_lies_over_paths(empty: FeatureStore) -> None:
    """An open platform covers the dotted path it crosses."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    for layer, pixels in (
        ('path', [(x + 20, y + 50.5), (x + 200, y + 50.5)]),
        ('platform', [(x + 100.0, y + 10), (x + 100.0, y + 240)]),
    ):
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=SEAM_MPP)
    tile = draw_window(store=empty, window=window)
    assert (tile[50, 99:101] == DARK).all()
    assert (tile[50, 20:90] == INK).any()
