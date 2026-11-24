"""Tests for map base: redrawn names."""

import math
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.features import FeatureStore
from paperroute_transit.build.map_style import (
    DARK,
    LIGHT,
    STREET_FACE,
    SUPERSAMPLE,
)
from paperroute_transit.build.tile_draw import Window as DrawWindow
from paperroute_transit.build.tile_draw import draw_window
from paperroute_transit.build.tile_store import PlacedLabel
from paperroute_transit.core.map_view import MAP_WIDTH
from paperroute_transit.core.tiles import TILE, TileVariant, to_pixels
from paperroute_transit.render.map_base import (
    HALO,
    draw_map_base,
    name_region,
    redraw_name,
    window_of,
)
from tests.render.map_base.helpers import (
    CENTRE,
    LABEL_SHIFT,
    MPP,
    OFFSET,
    across,
    base,
    build,
    centre_row,
    street,
)


def draw_with(
    *,
    out: Path,
    labels: Sequence[PlacedLabel],
) -> NDArray[np.uint8]:
    """
    Draw the base with an east-west route over a tiny tiles file.

    Parameters
    ----------
    out : Path
        Tiles file to write.
    labels : Sequence[PlacedLabel]
        Labels to store.

    Returns
    -------
    NDArray[np.uint8]
        The drawn base.
    """
    with closing(build(out=out, labels=labels)) as tiles:
        return draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )


def test_names_over_the_line_are_redrawn(tmp_path: Path) -> None:
    """A street along the route is redrawn over the stroke."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + LABEL_SHIFT)
    box = (slice(row - 3, row + 4), slice(x - 30, x + 31))
    assert np.count_nonzero(named[box] == DARK) > 0
    assert np.count_nonzero(plain[box] == DARK) == 0
    assert np.count_nonzero(named[box] == PAPER) > np.count_nonzero(
        plain[box] == PAPER,
    )
    assert set(np.unique(plain[box]).tolist()) == {INK}


def test_route_street_names_redrawn_in_ink(tmp_path: Path) -> None:
    """A name redrawn along the route is ink letters over the stroke."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + LABEL_SHIFT)
    edge = (slice(row - 5, row - 2), slice(x - 30, x + 31))
    assert (plain[edge] == INK).all()
    assert np.count_nonzero(named[edge] == INK) > 0
    assert np.count_nonzero(named[edge] == PAPER) > 0


def smooth_name(
    *,
    under: NDArray[np.uint8],
    label: PlacedLabel,
    offset: tuple[int, int],
) -> NDArray[np.uint8]:
    """
    Redraw a street name over a copy of map pixels.

    Parameters
    ----------
    under : NDArray[np.uint8]
        The map without the name.
    label : PlacedLabel
        The label, in global pixels.
    offset : tuple[int, int]
        The map's top-left corner in global pixels.

    Returns
    -------
    NDArray[np.uint8]
        The pixels of the name's region.
    """
    grey = under.copy()
    redraw_name(grey=grey, label=label, offset=offset)
    return grey[name_region(label=label, offset=offset)]


def test_redrawn_name_matches_the_baked_name(tmp_path: Path) -> None:
    """A name redrawn over paper equals the same label baked in a tile."""
    label = street(name='Church Street', x=1203.3, y=2140.6, angle=11.0)
    store = FeatureStore(path=tmp_path / 'features.db')
    with closing(store.conn):
        store.add_labels(labels=[label])
        baked = draw_window(
            store=store,
            window=DrawWindow(
                mpp=MPP,
                left=OFFSET[0],
                top=OFFSET[1],
                width=TILE,
                height=TILE,
                projection=SYDNEY_PROJECTION,
            ),
        )
    redrawn = np.full((TILE, TILE), PAPER, dtype=np.uint8)
    redraw_name(grey=redrawn, label=label, offset=OFFSET)
    assert (baked == LIGHT).any()
    np.testing.assert_array_equal(redrawn, baked)


@pytest.mark.parametrize('angle', [0.0, 17.0, 45.0, 90.0])
@pytest.mark.parametrize(
    'name',
    ['Church Street', 'Jaguar Way', 'Great Western Highway Service Road'],
)
def test_name_region_holds_the_whole_smoothed_name(
    name: str,
    angle: float,
) -> None:
    """Every pixel a 4x name and its halo shade lies inside its region."""
    for shift in (0.0, 0.3, 0.6):
        label = street(name=name, x=300.0 + shift, y=300.7 - shift)
        label = replace(label, angle=angle)
        canvas = Canvas(width=600, height=600, scale=SUPERSAMPLE, smooth=True)
        canvas.rect(box=(0, 0, 600, 600), grey=LIGHT)
        canvas.text(
            at=(label.x, label.y),
            text=name,
            face=STREET_FACE,
            grey=DARK,
            anchor='mm',
            halo=HALO,
            angle=angle,
        )
        shaded = canvas.grey() != LIGHT
        shaded[name_region(label=label, offset=(0, 0))] = False
        assert not shaded.any()


@pytest.mark.parametrize('angle', [0.0, 3.0])
def test_redrawn_names_are_smoothed_like_baked_ones(
    tmp_path: Path,
    angle: float,
) -> None:
    """A redrawn name is drawn at 4x, averaged and snapped to 4 levels."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(
        name='Church Street',
        x=gx + LABEL_SHIFT + 0.3,
        y=gy + 0.6,
        angle=angle,
    )
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    offset = window_of(base=base())
    region = name_region(label=label, offset=offset)
    np.testing.assert_array_equal(
        named[region],
        smooth_name(under=plain, label=label, offset=offset),
    )
    assert np.count_nonzero(named[region] == LIGHT) > 0
    outside = np.ones(named.shape, dtype=bool)
    outside[region] = False
    np.testing.assert_array_equal(named[outside], plain[outside])


def test_crossing_names_are_not_redrawn(tmp_path: Path) -> None:
    """A street crossing the route is left under the line."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(name='George St', x=gx + LABEL_SHIFT, y=gy, angle=90.0)
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    assert np.array_equal(named, plain)
