"""Tests for tile draw: pixels."""

import math

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.map_style import DARK, LIGHT
from paperroute_transit.build.tile_draw import HALO, draw_window
from paperroute_transit.core.tiles import LabelKind
from tests.build.tile_draw.helpers import (
    SEAM_MPP,
    add_pixel_area,
    add_pixel_line,
    park_under_tile,
    point_label,
    street_label,
    tile_window,
)


def rimmed(*, side: int) -> NDArray[np.uint8]:
    """
    Give an ink square inside a one pixel paper rim.

    Parameters
    ----------
    side : int
        The ink square's side in pixels.

    Returns
    -------
    NDArray[np.uint8]
        A square ``side + 2`` pixels across.
    """
    ink = np.full((side, side), INK, dtype=np.uint8)
    return np.pad(ink, 1, constant_values=PAPER)


def test_one_pixel_roads_land_on_exact_pixels(empty: FeatureStore) -> None:
    """One pixel roads land on exact pixels."""
    window = tile_window(mpp=14)
    x, y = float(window.left), float(window.top)
    for pixels in (
        [(x - 20, y + 100.5), (x + 300, y + 100.5)],
        [(x + 60.5, y - 20), (x + 60.5, y + 300)],
    ):
        add_pixel_line(
            store=empty,
            layer='road_minor',
            pixels=pixels,
            mpp=14,
        )
    tile = draw_window(store=empty, window=window)
    assert (tile[100] == LIGHT).all()
    assert (tile[:, 60] == LIGHT).all()
    assert (tile[[99, 101], :60] == PAPER).all()
    assert (tile[:100, [59, 61]] == PAPER).all()


def test_road_between_pixels_shades_its_edges(empty: FeatureStore) -> None:
    """Road between pixels shades its edges."""
    window = tile_window(mpp=14)
    x, y = float(window.left), float(window.top)
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[(x - 20, y + 100.25), (x + 300, y + 100.25)],
        mpp=14,
    )
    tile = draw_window(store=empty, window=window)
    profile = [PAPER, LIGHT, DARK, DARK, LIGHT, PAPER]
    assert (tile[97:103] == np.array(profile)[:, None]).all()


def test_diagonal_road_edges_are_smoothed(empty: FeatureStore) -> None:
    """Diagonal road edges are smoothed."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    add_pixel_line(
        store=empty,
        layer='road_primary',
        pixels=[(x + 20, y + 20), (x + 220, y + 120)],
        mpp=SEAM_MPP,
    )
    tile = draw_window(store=empty, window=window)
    along = [
        tile[20 + math.floor((step + 0.5) / 2), 20 + step]
        for step in range(200)
    ]
    assert set(along) == {DARK}
    assert (tile == LIGHT).any()


def test_smoothed_names_stay_inside_their_width(
    empty: FeatureStore,
) -> None:
    """Smoothed names stay inside their width."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    labels = [
        street_label(name=name, centre=(x + 128, y + row), angle=0.0)
        for name, row in (
            ('Church Street', 30),
            ('Jaguar Way', 70),
            ('jjj', 110),
            ('Fits ' * 18, 150),
            ('Great Western Highway Service Road', 200),
        )
    ]
    add_pixel_area(
        store=empty,
        layer='water',
        box=(x - 10, y - 10, x + 300, y + 300),
        mpp=SEAM_MPP,
    )
    empty.add_labels(labels=labels)
    tile = draw_window(store=empty, window=window)
    for label in labels:
        middle = round(label.y) - window.top
        rows = slice(middle - 10, middle + 10)
        drawn = np.nonzero((tile[rows] != DARK).any(axis=0))[0]
        half = label.width / 2 + HALO
        assert drawn.min() >= 128 - half
        assert drawn.max() + 1 <= 128 + half


def test_station_squares_land_on_exact_pixels(empty: FeatureStore) -> None:
    """Station squares land on exact pixels."""
    window, x, y = park_under_tile(store=empty)
    empty.add_labels(
        labels=[
            point_label(
                name='A',
                kind=LabelKind.STATION,
                point=(x + 40, y + 140),
                rank=0,
            ),
            point_label(
                name='B',
                kind=LabelKind.STATION,
                point=(x + 200, y + 140),
                rank=1,
            ),
        ],
    )
    tile = draw_window(store=empty, window=window)
    assert (tile[134:146, 34:46] == rimmed(side=10)).all()
    assert (tile[136:144, 196:204] == rimmed(side=6)).all()
    assert (tile[[133, 146], 34:46] == LIGHT).all()
    assert (tile[135, 196:204] == LIGHT).all()
