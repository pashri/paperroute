"""Tests for tile draw: seams."""

import math
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.map_style import DARK
from paperroute_transit.build.tile_draw import Window, draw_window
from paperroute_transit.core.tiles import LABEL_REACH, TILE, LabelKind
from paperroute_transit.render.tile_codec import GREYS
from tests.build.tile_draw.helpers import (
    SEAM_ANGLE,
    SEAM_MPP,
    add_pixel_line,
    origin_window,
    point_label,
    street_label,
    tile_pixel,
)

DIAGONAL_SLACK: Final[int] = 12  # pixels per sloping road crossing a seam


def seam_windows(*, mpp: int) -> tuple[Window, Window, Window]:
    """
    Give two side-by-side tiles near Constitution Hill and their union.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[Window, Window, Window]
        The left tile, the right tile and the 512 by 256 window.
    """
    window, _ = tile_pixel(lon=151.0, lat=-33.8, mpp=mpp)
    right = Window(
        mpp=mpp,
        left=window.left + TILE,
        top=window.top,
        width=TILE,
        height=TILE,
        projection=SYDNEY_PROJECTION,
    )
    whole = Window(
        mpp=mpp,
        left=window.left,
        top=window.top,
        width=2 * TILE,
        height=TILE,
        projection=SYDNEY_PROJECTION,
    )
    return window, right, whole


def stitched(
    *,
    store: FeatureStore,
    mpp: int,
) -> tuple[NDArray[np.uint8], NDArray[np.uint8]]:
    """
    Draw two neighbouring tiles side by side, and the window they fill.

    Parameters
    ----------
    store : FeatureStore
        The features.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[NDArray[np.uint8], NDArray[np.uint8]]
        The stitched tiles and the whole window.
    """
    left, right, whole = seam_windows(mpp=mpp)
    tiles = np.hstack(
        (
            draw_window(store=store, window=left),
            draw_window(store=store, window=right),
        ),
    )
    return tiles, draw_window(store=store, window=whole)


def edges(*, grey: NDArray[np.uint8]) -> NDArray[np.bool_]:
    """
    Find the pixels whose 3 by 3 neighbourhood is not one level.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        Grey levels.

    Returns
    -------
    NDArray[np.bool_]
        True where a pixel sits on an edge.
    """
    padded = np.pad(grey, 1, mode='edge')
    around = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return np.asarray(around.max(axis=(2, 3)) != around.min(axis=(2, 3)))


def assert_nearly_seamless(
    *,
    tiles: NDArray[np.uint8],
    whole: NDArray[np.uint8],
    slack: int,
) -> None:
    """
    Check stitched tiles match the whole window but for a few edge pixels.

    Smoothed sloping edges are cut at each canvas's border, so a pixel
    whose shade sits on a level boundary can round either way. Each
    such pixel must be one level apart and lie on an edge.

    Parameters
    ----------
    tiles : NDArray[np.uint8]
        The stitched tiles.
    whole : NDArray[np.uint8]
        The whole window.
    slack : int
        Most pixels allowed to differ.
    """
    gap = np.abs(tiles.astype(np.int16) - whole.astype(np.int16))
    assert int(gap.max()) <= GREYS[1]
    assert not (gap.astype(bool) & ~edges(grey=whole)).any()
    assert np.count_nonzero(gap) <= slack


def test_label_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    """Label crossing a tile edge is seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    edge = left.left + TILE
    label = street_label(
        name='Seam Street',
        centre=(float(edge), float(left.top + TILE // 2)),
        angle=SEAM_ANGLE,
    )
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[label.start, label.end],
        mpp=SEAM_MPP,
    )
    empty.add_labels(labels=[label])
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - TILE // 4 : TILE + TILE // 4] == INK).any()
    assert_nearly_seamless(tiles=tiles, whole=whole, slack=1)


def test_turned_label_crossing_a_tile_edge_is_exact(
    empty: FeatureStore,
) -> None:
    """Turned label crossing a tile edge is exact."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    label = street_label(
        name='Seam Street',
        centre=(float(left.left + TILE), float(left.top + TILE // 2)),
        angle=SEAM_ANGLE,
    )
    empty.add_labels(labels=[label])
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 8 : TILE + 8] == INK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_sloping_road_crossing_a_tile_edge_is_nearly_seamless(
    empty: FeatureStore,
) -> None:
    """Sloping road crossing a tile edge is nearly seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left + TILE), float(left.top + TILE // 2)
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[(x - 150, y - 55), (x + 150, y + 55)],
        mpp=SEAM_MPP,
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 2 : TILE + 2] == DARK).any()
    assert_nearly_seamless(tiles=tiles, whole=whole, slack=DIAGONAL_SLACK)


def test_seven_sloping_roads_crossing_a_tile_edge_are_nearly_seamless(
    empty: FeatureStore,
) -> None:
    """Seven sloping roads crossing a tile edge are nearly seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left + TILE), float(left.top)
    roads = (
        ('road_minor', 5.0),
        ('road_tertiary', 17.0),
        ('road_secondary', 29.0),
        ('road_primary', 41.0),
        ('road_trunk', 57.0),
        ('road_motorway', 69.0),
        ('river', 81.0),
    )
    for step, (layer, angle) in enumerate(roads):
        turn = math.radians(angle)
        reach = (150 * math.cos(turn), 150 * math.sin(turn))
        middle = y + 20.3 + 32 * step
        add_pixel_line(
            store=empty,
            layer=layer,
            pixels=[
                (x - reach[0], middle - reach[1]),
                (x + reach[0], middle + reach[1]),
            ],
            mpp=SEAM_MPP,
        )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 2 : TILE + 2] == DARK).sum() >= len(roads)
    assert_nearly_seamless(
        tiles=tiles,
        whole=whole,
        slack=len(roads) * DIAGONAL_SLACK,
    )


def test_roads_beside_a_tile_edge_are_seamless(
    empty: FeatureStore,
) -> None:
    """Roads beside a tile edge are seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left + TILE), float(left.top)
    lines = (
        ('road_motorway', [(x + 2.5, y - 20), (x + 2.5, y + 120)]),
        ('road_motorway', [(x - 2.5, y + 140), (x - 2.5, y + 280)]),
        ('river', [(x - 100, y + 64.5), (x + 100, y + 64.5)]),
    )
    for layer, pixels in lines:
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=SEAM_MPP)
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole[:, TILE - 2] == DARK).any()
    assert (whole[:, TILE + 1] == DARK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_long_label_reaches_the_next_tile(empty: FeatureStore) -> None:
    """Long label reaches the next tile."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    label = street_label(
        name='Great Western Highway Northern Service Road East',
        centre=(float(left.left + TILE - 80), float(left.top + 100)),
        angle=0.0,
    )
    empty.add_labels(labels=[label])
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert label.width / 2 > 80 + 40
    assert (whole[:, TILE + 40 :] == INK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_label_too_wide_for_the_reach_is_not_drawn(
    empty: FeatureStore,
) -> None:
    """Label too wide for the reach is not drawn."""
    window = origin_window(mpp=SEAM_MPP)
    wide = street_label(name='Wide ' * 60, centre=(128.0, 64.0), angle=0.0)
    fits = street_label(name='Fits ' * 18, centre=(128.0, 192.0), angle=0.0)
    empty.add_labels(labels=[wide, fits])
    tile = draw_window(store=empty, window=window)
    assert wide.width / 2 + 2 > LABEL_REACH
    assert fits.width / 2 + 2 < LABEL_REACH
    assert (tile[: TILE // 2] == PAPER).all()
    assert (tile[TILE // 2 :] == INK).any()


def test_crossing_transit_is_seamless(empty: FeatureStore) -> None:
    """Crossing transit is seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left), float(left.top)
    lines = (
        ('rail', [(x + 10, y + 50.5), (x + 90, y + 50.5)]),
        ('light_rail', [(x + 276.5, y - 10), (x + 276.5, y + 260)]),
        ('rail', [(x + 200, y + 128.5), (x + 346, y + 128.5)]),
    )
    for layer, pixels in lines:
        add_pixel_line(store=empty, layer=layer, pixels=pixels, mpp=SEAM_MPP)
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    np.testing.assert_array_equal(tiles, whole)


def test_ticked_light_rail_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    """Ticks line up across a tile edge and stand clear of each other."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    middle = left.top + TILE // 2 + 0.5
    add_pixel_line(
        store=empty,
        layer='light_rail',
        pixels=[(left.left - 100.0, middle), (left.left + 600.0, middle)],
        mpp=SEAM_MPP,
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    row = int(middle) - left.top
    assert (whole[row] == INK).all()
    assert (whole[row - 2] == INK).any()
    assert (whole[row - 2] == PAPER).any()
    np.testing.assert_array_equal(tiles, whole)


def test_dotted_path_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    """A sloping path's dots line up across a tile edge."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    x, y = float(left.left), float(left.top)
    add_pixel_line(
        store=empty,
        layer='path',
        pixels=[(x - 101.0, y + 20.5), (x + 600.0, y + 230.5)],
        mpp=SEAM_MPP,
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole == INK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_points_crossing_a_tile_edge_are_seamless(
    empty: FeatureStore,
) -> None:
    """Points crossing a tile edge are seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    edge = left.left + TILE
    empty.add_labels(
        labels=[
            point_label(
                name='Central',
                kind=LabelKind.STATION,
                point=(edge + 0.3, left.top + 60.6),
                rank=0,
            ),
            point_label(
                name='Halt',
                kind=LabelKind.STATION,
                point=(edge - 2.4, left.top + 140.2),
                rank=1,
            ),
            point_label(
                name='C O N S T I T U T I O N',
                kind=LabelKind.SUBURB,
                point=(edge + 1.0, left.top + 200.0),
                rank=0,
            ),
        ],
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    assert (whole == INK).any()
    assert (whole == DARK).any()
    np.testing.assert_array_equal(tiles, whole)


def test_dashed_rail_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    """Dashed rail crossing a tile edge is seamless."""
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    middle = left.top + TILE // 2 + 0.5
    add_pixel_line(
        store=empty,
        layer='rail',
        pixels=[(left.left - 100.0, middle), (left.left + 600.0, middle)],
        mpp=SEAM_MPP,
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    line = whole[int(middle) - left.top]
    assert (line == INK).any()
    assert (line == PAPER).any()
    np.testing.assert_array_equal(tiles, whole)
