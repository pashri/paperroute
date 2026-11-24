"""Tests for map view: markers and text."""

import math
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Face,
    text_width,
)
from paperroute_device.render.frame import HEADER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.build.tile_store import TileWriter
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    screen_point,
)
from paperroute_transit.core.tiles import (
    CITY,
    TILE,
    ZOOMS,
    TileVariant,
    tile_of,
    to_pixels,
)
from paperroute_transit.render.map_base import draw_map_base
from paperroute_transit.render.map_view import (
    EMPTY_MARGIN,
    SIDEBAR_LEFT,
    draw_map_view,
    empty_lines,
    snap,
)
from paperroute_transit.render.tile_codec import GREYS, pack_bits, pack_grey
from tests.render.map_view.helpers import (
    CENTRE,
    LONG_EMPTY,
    MPP,
    VIEW,
    live,
    paper,
    stop_pixel,
    stop_point,
)

EMPTY = 'No more services today'


def test_stop_marker_is_drawn_over_badges() -> None:
    """A badge stuck on the stop is covered by the stop marker again."""
    view = replace(VIEW, markers=(live(at=stop_point()),) * 40)
    frame = draw_map_view(view=view, base=paper())
    sx, sy = stop_pixel()
    assert frame[sy, sx] == INK
    assert frame[sy, sx + 7] == PAPER
    assert frame[sy, sx + 12] == INK
    assert frame[sy, sx - 12] == INK
    assert frame[sy, sx + 15] == PAPER
    assert frame[sy, sx - 15] == PAPER


def test_of_crowded_badges_the_one_nearest_the_stop_shows() -> None:
    """Two overlapping badges draw only the one nearer the From."""
    x, y = screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=CENTRE,
        projection=SYDNEY_PROJECTION,
    )
    far = live(at=(x + 100, y + 60))
    near = live(at=(x + 100, y + 52))
    both = draw_map_view(view=replace(VIEW, markers=(far, near)), base=paper())
    alone = draw_map_view(view=replace(VIEW, markers=(near,)), base=paper())
    assert (both == alone).all()


def empty_band(*, frame: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """
    Cut out the rows below the stop where the empty text goes.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A Map frame.

    Returns
    -------
    NDArray[np.uint8]
        The map's rows from 40 to 70 px below its centre.
    """
    middle = HEADER + MAP_HEIGHT // 2
    return frame[middle + 40 : middle + 70, :SIDEBAR_LEFT]


def test_empty_text_sits_below_the_stop() -> None:
    """The empty text is clear of the stop marker, which stays whole."""
    frame = draw_map_view(view=replace(VIEW, empty=EMPTY), base=paper())
    assert np.any(empty_band(frame=frame) == INK)
    sx, sy = stop_pixel()
    assert np.all(frame[sy - 20 : sy + 20, 60 : sx - 20] == PAPER)
    assert np.all(frame[sy - 20 : sy + 20, sx + 20 : 460] == PAPER)
    assert frame[sy, sx] == INK
    assert frame[sy, sx + 7] == PAPER
    assert frame[sy, sx + 12] == INK


def test_empty_text_has_a_halo() -> None:
    """Over a dark map the empty text is ringed with paper."""
    dark = np.full((MAP_HEIGHT, MAP_WIDTH), INK, dtype=np.uint8)
    frame = draw_map_view(view=replace(VIEW, empty=EMPTY), base=dark)
    assert np.any(empty_band(frame=frame) == PAPER)


def test_no_location_says_so_without_a_stop() -> None:
    """With no centre the empty text shows and no stop is drawn."""
    view = replace(
        VIEW,
        base=MapBase(
            centre=None,
            mpp=MPP,
            route=(),
            projection=SYDNEY_PROJECTION,
        ),
        empty='No location for this stop',
    )
    frame = draw_map_view(view=view, base=paper())
    assert np.any(empty_band(frame=frame) == INK)
    plain = draw_map_view(view=replace(view, empty=None), base=paper())
    assert np.all(plain[HEADER:, :SIDEBAR_LEFT] == PAPER)


def test_snap_thresholds_outside_the_map() -> None:
    """Grey outside the map becomes ink or paper; inside, a level."""
    out = snap(grey=np.full((HEIGHT, WIDTH), 100, dtype=np.uint8))
    assert np.all(out[:HEADER] == INK)
    assert np.all(out[:, SIDEBAR_LEFT:] == INK)
    assert np.all(out[HEADER:, :SIDEBAR_LEFT] == GREYS[1])


def dot_tile_base(
    *, tmp_path: Path, point: Point,
) -> tuple[MapBase, NDArray[np.uint8]]:
    """
    Draw a base from a tile holding one ink pixel at a metre point.

    Parameters
    ----------
    tmp_path : Path
        Working directory for the tile file.
    point : Point
        Metre point of the ink pixel.

    Returns
    -------
    tuple[MapBase, NDArray[np.uint8]]
        The map base and the grey frame drawn from the tile.
    """
    gx, gy = to_pixels(point=point, mpp=CITY, projection=SYDNEY_PROJECTION)
    col, tile_row = tile_of(pixel=(gx, gy))
    dot = np.full((TILE, TILE), PAPER, dtype=np.uint8)
    dot[math.floor(gy) - tile_row * TILE, math.floor(gx) - col * TILE] = INK
    with TileWriter(out=tmp_path / 'tiles.db') as writer:
        writer.add_tile(
            zoom=CITY,
            col=col,
            row=tile_row,
            grey=pack_grey(grey=dot),
            bits=pack_bits(paper=dot >= 128),
        )
        writer.finish(meta={})
    city = MapBase(
        centre=CENTRE,
        mpp=CITY,
        route=(),
        projection=SYDNEY_PROJECTION,
    )
    with closing(SqliteTileRepository(path=tmp_path / 'tiles.db')) as tiles:
        base = draw_map_base(base=city, tiles=tiles, variant=TileVariant.GREY)
    return city, base


def test_markers_and_tiles_share_one_transform(tmp_path: Path) -> None:
    """A tile pixel and a dot marker at one metre point coincide."""
    point = (CENTRE[0] + 4000.0, CENTRE[1] - 3000.0)
    city, base = dot_tile_base(tmp_path=tmp_path, point=point)
    view = replace(VIEW, base=city, zoom=ZOOMS.index(CITY))
    tiled = draw_map_view(view=view, base=base)
    sx, sy = screen_point(
        centre=CENTRE,
        mpp=CITY,
        point=point,
        projection=SYDNEY_PROJECTION,
    )
    ty, tx = math.floor(sy) + HEADER, math.floor(sx)
    assert tiled[ty, tx] == INK
    marked = draw_map_view(
        view=replace(
            view, markers=(replace(live(at=(sx, sy)), pinned=False),),
        ),
        base=paper(),
    )
    near = marked[ty - 10 : ty + 11, tx - 10 : tx + 11]
    centre = np.argwhere(near == INK).mean(axis=0) + (ty - 10, tx - 10)
    assert np.allclose(centre, (ty + 0.5, tx + 0.5), atol=1.0)


def test_base_is_not_mutated() -> None:
    """Drawing the frame leaves the cached base untouched."""
    base = np.tile(
        np.array(GREYS, dtype=np.uint8),
        (MAP_HEIGHT, MAP_WIDTH // len(GREYS)),
    )
    kept = base.copy()
    view = replace(
        VIEW,
        markers=(live(at=(100.0, 100.0)),),
        banner='Showing timetable only',
    )
    draw_map_view(view=view, base=base)
    assert np.array_equal(base, kept)


def test_long_empty_text_wraps_to_two_whole_lines() -> None:
    """A long empty text splits into two lines that fit, never cut."""
    lines, face = empty_lines(text=LONG_EMPTY)
    assert len(lines) == 2
    assert ' '.join(lines) == LONG_EMPTY
    assert all(
        text_width(text=line, face=face) <= MAP_WIDTH - 2 * EMPTY_MARGIN
        for line in lines
    )


def test_long_empty_text_draws_a_second_line_inside_the_map() -> None:
    """The second line is drawn below the first and inside the map."""
    middle = HEADER + MAP_HEIGHT // 2
    second = (slice(middle + 72, middle + 96), slice(0, SIDEBAR_LEFT))
    short = draw_map_view(view=replace(VIEW, empty=EMPTY), base=paper())
    long = draw_map_view(view=replace(VIEW, empty=LONG_EMPTY), base=paper())
    assert not np.any(short[second] == INK)
    assert np.any(long[second] == INK)
    ink = np.flatnonzero(np.any(long[HEADER:, :SIDEBAR_LEFT] == INK, axis=0))
    assert ink.min() >= EMPTY_MARGIN - 4
    assert ink.max() <= MAP_WIDTH - EMPTY_MARGIN + 4


def test_empty_text_is_drawn_over_markers() -> None:
    """A badge under the empty text does not hide it."""
    x, y = stop_pixel()
    bus = live(at=(x - 120.0, y - HEADER + 52.0))
    view = replace(VIEW, markers=(bus,))
    badge = draw_map_view(view=view, base=paper())
    both = draw_map_view(view=replace(view, empty=EMPTY), base=paper())
    under = badge == INK
    under[:HEADER] = False
    under[:, SIDEBAR_LEFT:] = False
    assert np.any(under)
    assert np.any(both[under] == PAPER)


def test_empty_text_too_long_for_two_lines_uses_the_smallest_face() -> None:
    """Text that never fits two lines is wrapped whole at the smallest."""
    text = ' '.join(['Parramatta'] * 30)
    lines, face = empty_lines(text=text)
    assert face == Face(weight=700, px=15)
    assert ' '.join(lines) == text
