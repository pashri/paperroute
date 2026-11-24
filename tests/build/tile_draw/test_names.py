"""Tests for tile draw: names."""

from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas, Face
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.map_style import (
    DARK,
    STATION_FACE,
    STREET_FACE,
    SUBURB_FACE,
    SUPERSAMPLE,
)
from paperroute_transit.build.tile_draw import Window, draw_window
from paperroute_transit.build.tile_store import PlacedLabel
from paperroute_transit.core.tiles import TILE, LabelKind
from paperroute_transit.render.tile_codec import quantise4
from tests.build.tile_draw.helpers import (
    SEAM_ANGLE,
    SEAM_MPP,
    park_under_tile,
    point_label,
    street_label,
    tile_window,
)

NAME_STYLES: Final[dict[LabelKind, tuple[Face, int]]] = {
    LabelKind.STATION: (STATION_FACE, INK),
    LabelKind.SUBURB: (SUBURB_FACE, DARK),
    LabelKind.STREET: (STREET_FACE, INK),
}


def reference(
    *,
    window: Window,
    labels: list[PlacedLabel],
) -> NDArray[np.uint8]:
    """
    Draw names smoothly at 4x onto a canvas at the window's local pixels.

    Parameters
    ----------
    window : Window
        The window the labels are drawn for.
    labels : list[PlacedLabel]
        Labels in drawing order; station squares are left out.

    Returns
    -------
    NDArray[np.uint8]
        The four-level pixels.
    """
    canvas = Canvas(
        width=window.width,
        height=window.height,
        scale=SUPERSAMPLE,
        smooth=True,
    )
    for label in labels:
        canvas.text(
            at=(label.x - window.left, label.y - window.top),
            text=label.name,
            face=NAME_STYLES[label.kind][0],
            grey=NAME_STYLES[label.kind][1],
            anchor='mm',
            halo=2,
            angle=label.angle,
        )
    return quantise4(grey=canvas.grey())


def test_names_land_where_the_canvas_puts_them(empty: FeatureStore) -> None:
    """Names land where the canvas puts them."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    station = point_label(
        name='Central',
        kind=LabelKind.STATION,
        point=(x + 130, y + 112),
        rank=0,
    )
    labels = [
        replace(station, start=(x - TILE, y - TILE)),
        point_label(
            name='C O N S T I T U T I O N',
            kind=LabelKind.SUBURB,
            point=(x + 120, y + 100),
            rank=0,
        ),
        street_label(
            name='Crossing Street',
            centre=(x + 128, y + 104),
            angle=SEAM_ANGLE,
        ),
    ]
    empty.add_labels(labels=list(reversed(labels)))
    tile = draw_window(store=empty, window=window)
    np.testing.assert_array_equal(
        tile,
        reference(window=window, labels=labels),
    )
    assert not np.array_equal(
        tile,
        reference(window=window, labels=labels[::-1]),
    )


def test_halo_clears_paper_around_names(empty: FeatureStore) -> None:
    """Halo clears paper around names."""
    window, x, y = park_under_tile(store=empty)
    empty.add_labels(
        labels=[
            street_label(
                name='Halo Street',
                centre=(x + 128, y + 128),
                angle=0.0,
            ),
        ],
    )
    tile = draw_window(store=empty, window=window)
    assert (tile == INK).any()
    assert (tile == PAPER).any()


def test_street_names_baked_in_ink(empty: FeatureStore) -> None:
    """Street names baked in ink."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    label = street_label(
        name='Ink Street',
        centre=(x + 128, y + 128),
        angle=0.0,
    )
    empty.add_labels(labels=[label])
    tile = draw_window(store=empty, window=window)
    assert (tile[120:137, 80:177] == INK).any()
    np.testing.assert_array_equal(
        tile,
        reference(window=window, labels=[label]),
    )


def test_suburb_names_stay_dark(empty: FeatureStore) -> None:
    """Suburb names stay dark."""
    window = tile_window(mpp=SEAM_MPP)
    x, y = float(window.left), float(window.top)
    empty.add_labels(
        labels=[
            point_label(
                name='DARKTON',
                kind=LabelKind.SUBURB,
                point=(x + 128, y + 128),
                rank=0,
            ),
        ],
    )
    tile = draw_window(store=empty, window=window)
    assert tile.min() == DARK
