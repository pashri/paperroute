"""Tests for drawing map tiles from the scratch features."""

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.build.features import FeatureStore, load_features
from paperroute.build.map_style import DARK, LIGHT, STREET_FACE
from paperroute.build.tile_draw import Window, draw_window, render_tile
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.geo import MetreBox, to_metres
from paperroute.core.tiles import (
    TILE,
    LabelKind,
    from_pixels,
    tile_of,
    to_pixels,
)
from paperroute.render.canvas import INK, PAPER, text_width
from paperroute.render.tile_codec import GREYS, unpack_grey
from tests.build.osm_factory import TINY_BBOX, write_tiny_osm

FRAME_HALF: Final[float] = 800.0  # metres
SEAM_MPP: Final[int] = 3
SEAM_ANGLE: Final[float] = 20.0  # degrees


def around(*, lon: float, lat: float, half: float) -> MetreBox:
    """
    Build a metre box centred on a longitude and latitude.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    half : float
        Half the box's side in metres.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    x, y = to_metres(lon=lon, lat=lat)
    return (x - half, y - half, x + half, y + half)


def tile_pixel(
    *,
    lon: float,
    lat: float,
    mpp: int,
) -> tuple[Window, tuple[int, int]]:
    """
    Find the tile window holding a point and the point's pixel in it.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[Window, tuple[int, int]]
        The tile's window, and the column and row of the pixel holding
        the point.
    """
    pixel = to_pixels(point=to_metres(lon=lon, lat=lat), mpp=mpp)
    col, row = tile_of(pixel=pixel)
    window = Window(
        mpp=mpp,
        left=col * TILE,
        top=row * TILE,
        width=TILE,
        height=TILE,
    )
    local = (
        math.floor(pixel[0]) - window.left,
        math.floor(pixel[1]) - window.top,
    )
    return window, local


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


@pytest.fixture(name='store', scope='module')
def _store(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[FeatureStore]:
    """
    Load the tiny OSM file into a store.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Pytest's temporary directory factory.

    Yields
    ------
    FeatureStore
        The loaded store.
    """
    folder = tmp_path_factory.mktemp('tile_draw')
    write_tiny_osm(path=folder / 'tiny.osm.pbf')
    store = FeatureStore(path=folder / 'features.db')
    load_features(
        osm=folder / 'tiny.osm.pbf',
        bbox=TINY_BBOX,
        store=store,
        frame=around(lon=151.005, lat=-33.8, half=FRAME_HALF),
        index='flex_mem',
    )
    yield store
    store.conn.close()


@pytest.fixture(name='empty')
def _empty(tmp_path: Path) -> Iterator[FeatureStore]:
    """
    Open an empty scratch store.

    Parameters
    ----------
    tmp_path : Path
        Pytest's temporary directory.

    Yields
    ------
    FeatureStore
        The store.
    """
    store = FeatureStore(path=tmp_path / 'features.db')
    yield store
    store.conn.close()


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
    )
    whole = Window(
        mpp=mpp,
        left=window.left,
        top=window.top,
        width=2 * TILE,
        height=TILE,
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


def add_pixel_line(
    *,
    store: FeatureStore,
    layer: str,
    pixels: list[tuple[float, float]],
    mpp: int,
) -> None:
    """
    Add a line drawn in a zoom's global pixels.

    Parameters
    ----------
    store : FeatureStore
        Target store.
    layer : str
        Line layer.
    pixels : list[tuple[float, float]]
        Vertices in global pixels.
    mpp : int
        Zoom the pixels are in.
    """
    store.add_line(
        layer=layer,
        name='',
        points=np.array([from_pixels(pixel=p, mpp=mpp) for p in pixels]),
    )


def test_tile_has_four_levels_only(store: FeatureStore) -> None:
    window, _ = tile_pixel(lon=151.0, lat=-33.802, mpp=3)
    tile = draw_window(store=store, window=window)
    assert tile.shape == (TILE, TILE)
    assert tile.dtype == np.uint8
    assert set(np.unique(tile).tolist()) <= set(GREYS)


def test_primary_road_is_ink(store: FeatureStore) -> None:
    assert grey_at(store=store, lon=151.003, lat=-33.802, mpp=3) == INK


def test_minor_road_is_light_at_14(store: FeatureStore) -> None:
    assert grey_at(store=store, lon=151.003, lat=-33.8, mpp=14) == LIGHT


def test_park_is_light_and_sea_is_dark(store: FeatureStore) -> None:
    assert grey_at(store=store, lon=150.997, lat=-33.795, mpp=3) == LIGHT
    assert grey_at(store=store, lon=151.011, lat=-33.8, mpp=3) == DARK


def test_label_crossing_a_tile_edge_is_seamless(
    empty: FeatureStore,
) -> None:
    left, _, _ = seam_windows(mpp=SEAM_MPP)
    edge, middle = left.left + TILE, left.top + TILE // 2
    width = text_width(text='Seam Street', face=STREET_FACE) + 6
    turn = math.radians(SEAM_ANGLE)
    reach = (math.cos(turn) * width / 2, math.sin(turn) * width / 2)
    start = (edge - reach[0], middle - reach[1])
    end = (edge + reach[0], middle + reach[1])
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[start, end],
        mpp=SEAM_MPP,
    )
    empty.add_labels(
        labels=[
            PlacedLabel(
                name='Seam Street',
                x=float(edge),
                y=float(middle),
                angle=SEAM_ANGLE,
                width=width,
                start=start,
                end=end,
                zoom=SEAM_MPP,
                kind=LabelKind.STREET,
            ),
        ],
    )
    tiles, whole = stitched(store=empty, mpp=SEAM_MPP)
    seam = edge - left.left
    near = whole[:, seam - TILE // 4 : seam + TILE // 4]
    assert (near == DARK).any()
    np.testing.assert_array_equal(tiles, whole)


def point_label(
    *,
    name: str,
    kind: LabelKind,
    point: tuple[float, float],
    rank: int,
) -> PlacedLabel:
    """
    Build a level station or suburb label.

    Parameters
    ----------
    name : str
        The name.
    kind : LabelKind
        Station or suburb.
    point : tuple[float, float]
        The feature's point in global pixels.
    rank : int
        Place rank.

    Returns
    -------
    PlacedLabel
        The label, 15 px above a station's point and on a suburb's.
    """
    rise = 15 if kind == LabelKind.STATION else 0
    return PlacedLabel(
        name=name,
        x=float(round(point[0])),
        y=float(round(point[1] - rise)),
        angle=0.0,
        width=60.0,
        start=point,
        end=point,
        zoom=SEAM_MPP,
        kind=kind,
        rank=rank,
    )


def test_points_crossing_a_tile_edge_are_seamless(
    empty: FeatureStore,
) -> None:
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


def test_render_tile_round_trips(store: FeatureStore) -> None:
    window, _ = tile_pixel(lon=151.0, lat=-33.802, mpp=3)
    grey, bits = render_tile(
        store=store,
        mpp=3,
        col=window.left // TILE,
        row=window.top // TILE,
    )
    np.testing.assert_array_equal(
        unpack_grey(blob=grey),
        draw_window(store=store, window=window),
    )
    assert bits


def test_empty_window_is_paper(empty: FeatureStore) -> None:
    window = Window(mpp=8, left=0, top=0, width=TILE, height=TILE)
    tile = draw_window(store=empty, window=window)
    assert (tile == PAPER).all()
