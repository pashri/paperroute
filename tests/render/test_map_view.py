"""Facts about the drawn Map view frame (no golden images)."""

import math
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import SqliteTileRepository
from paperroute.build.tile_store import TileWriter
from paperroute.core.geo import Point, to_metres
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    MapView,
    Marker,
    screen_point,
)
from paperroute.core.model import Mode
from paperroute.core.stop_view import Row
from paperroute.core.tiles import CITY, TILE, TileVariant, tile_of, to_pixels
from paperroute.render.canvas import HEIGHT, INK, PAPER, WIDTH, Canvas
from paperroute.render.chrome import HEADER, header
from paperroute.render.map_base import draw_map_base
from paperroute.render.map_view import SIDEBAR_LEFT, draw_map_view
from paperroute.render.tile_codec import GREYS, pack_bits, pack_grey

MPP = 8
CENTRE: Point = to_metres(lon=151.0036, lat=-33.8170)
ROW_TOPS = (110, 182, 254, 326, 398)


def row(*, route: str = '606') -> Row:
    """
    Make a sidebar row.

    Parameters
    ----------
    route : str
        Route label.

    Returns
    -------
    Row
        A live bus row.
    """
    return Row(
        route=route,
        mode=Mode.BUS,
        platform='',
        destination='Parramatta',
        countdown='9',
        leaves_at='8:09',
        arrives_at='8:45',
        ride_minutes=36,
        live=True,
        pill=None,
    )


VIEW = MapView(
    origin='Hollis St Opp Constitution Hill Library',
    destination='Parramatta Station',
    clock='8:00',
    base=MapBase(centre=CENTRE, mpp=MPP, route=()),
    zoom=2,
    markers=(),
    sidebar=(row(),) * 5,
    banner=None,
    empty=None,
)


def paper() -> NDArray[np.uint8]:
    """
    Make a blank map base.

    Returns
    -------
    NDArray[np.uint8]
        Map-sized paper.
    """
    return np.full((MAP_HEIGHT, MAP_WIDTH), PAPER, dtype=np.uint8)


def live(*, at: tuple[float, float], route: str = '606') -> Marker:
    """
    Make a live marker without a wedge.

    Parameters
    ----------
    at : tuple[float, float]
        Map pixels.
    route : str
        Route label.

    Returns
    -------
    Marker
        A pinned live marker.
    """
    return Marker(
        route=route,
        x=at[0],
        y=at[1],
        bearing=None,
        live=True,
        pinned=True,
    )


def stop_pixel() -> tuple[int, int]:
    """
    Find the frame pixel under the From stop.

    Returns
    -------
    tuple[int, int]
        Column and row.
    """
    x, y = screen_point(centre=CENTRE, mpp=MPP, point=CENTRE)
    return math.floor(x), math.floor(y + HEADER)


def test_map_frame_is_800_by_480_with_four_levels() -> None:
    """The map keeps four greys; everything else is ink or paper."""
    base = np.repeat(
        np.array(GREYS, dtype=np.uint8),
        MAP_WIDTH // len(GREYS),
    )
    frame = draw_map_view(view=VIEW, base=np.tile(base, (MAP_HEIGHT, 1)))
    assert frame.shape == (HEIGHT, WIDTH)
    assert frame.dtype == np.uint8
    assert set(np.unique(frame[HEADER:, :SIDEBAR_LEFT])) == set(GREYS)
    assert set(np.unique(frame[:HEADER])) <= {INK, PAPER}
    assert set(np.unique(frame[:, SIDEBAR_LEFT:])) <= {INK, PAPER}


def test_header_marks_the_map_tab() -> None:
    """The frame's header is the shared header with Map active."""
    canvas = Canvas()
    header(
        canvas,
        origin=VIEW.origin,
        destination=VIEW.destination,
        clock=VIEW.clock,
        active='Map',
    )
    expected = np.where(canvas.grey() < 128, INK, PAPER)
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.array_equal(frame[:HEADER], expected[:HEADER])


def test_rule_divides_map_and_sidebar() -> None:
    """A 3 px ink rule runs down from the header at x 520 to 523."""
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.all(frame[HEADER:, SIDEBAR_LEFT:SIDEBAR_LEFT + 3] == INK)
    assert np.all(frame[HEADER:, SIDEBAR_LEFT + 3] == PAPER)


def test_sidebar_draws_each_row() -> None:
    """Each row has a badge, a countdown, an arrival and a rule."""
    frame = draw_map_view(view=VIEW, base=paper())
    assert np.any(frame[56:74, 538:788] == INK)
    for top in ROW_TOPS:
        assert frame[top, 540] == INK
        assert np.any(frame[top - 14 : top + 13, 740:785] == INK)
        assert not np.any(frame[top - 14 : top + 13, 785:800] == INK)
        assert np.any(frame[top + 15 : top + 27, 538:600] == INK)
        assert np.all(frame[top + 40, 534:788] == INK)


def test_sidebar_stops_after_its_rows() -> None:
    """With two rows the rest of the sidebar is paper."""
    view = replace(VIEW, sidebar=(row(),) * 2)
    frame = draw_map_view(view=view, base=paper())
    assert np.all(frame[ROW_TOPS[2] - 20 :, SIDEBAR_LEFT + 3 :] == PAPER)


def test_markers_stay_inside_the_map() -> None:
    """A badge at the map's right edge never inks the sidebar."""
    plain = draw_map_view(view=VIEW, base=paper())
    marked = draw_map_view(
        view=replace(VIEW, markers=(live(at=(515.0, 200.0)),)),
        base=paper(),
    )
    changed = np.argwhere(plain != marked)
    assert changed.size
    assert changed[:, 1].max() < SIDEBAR_LEFT


def test_banner_is_drawn_over_the_map() -> None:
    """The banner's paper box covers a dark map."""
    dark = np.full((MAP_HEIGHT, MAP_WIDTH), INK, dtype=np.uint8)
    view = replace(VIEW, banner='Showing timetable only')
    assert draw_map_view(view=VIEW, base=dark)[HEADER + 6, 400] == INK
    assert draw_map_view(view=view, base=dark)[HEADER + 6, 400] == PAPER


def test_stop_marker_is_drawn_over_badges() -> None:
    """A badge stuck on the stop is covered by the stop marker again."""
    x, y = screen_point(centre=CENTRE, mpp=MPP, point=CENTRE)
    view = replace(VIEW, markers=(live(at=(x, y)),) * 40)
    frame = draw_map_view(view=view, base=paper())
    sx, sy = stop_pixel()
    assert frame[sy, sx] == INK
    assert frame[sy, sx + 6] == PAPER
    assert frame[sy, sx + 9] == INK
    assert frame[sy, sx - 9] == INK
    assert frame[sy, sx + 11] == PAPER
    assert frame[sy, sx - 11] == PAPER


def test_no_location_says_so_without_a_stop() -> None:
    """With no centre the empty text sits mid-map and no stop is drawn."""
    view = replace(
        VIEW,
        base=MapBase(centre=None, mpp=MPP, route=()),
        empty='No location for this stop',
    )
    frame = draw_map_view(view=view, base=paper())
    middle = HEADER + MAP_HEIGHT // 2
    assert np.any(frame[middle - 10 : middle + 10, 100:420] == INK)
    plain = draw_map_view(view=replace(view, empty=None), base=paper())
    assert np.all(plain[HEADER:, :SIDEBAR_LEFT] == PAPER)


def test_markers_and_tiles_share_one_transform(tmp_path: Path) -> None:
    """A tile pixel and a dot marker at one metre point coincide."""
    point = (CENTRE[0] + 4000.0, CENTRE[1] - 3000.0)
    gx, gy = to_pixels(point=point, mpp=CITY)
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
    city = MapBase(centre=CENTRE, mpp=CITY, route=())
    with closing(SqliteTileRepository(path=tmp_path / 'tiles.db')) as tiles:
        base = draw_map_base(base=city, tiles=tiles, variant=TileVariant.GREY)
    view = replace(VIEW, base=city, zoom=4)
    tiled = draw_map_view(view=view, base=base)
    sx, sy = screen_point(centre=CENTRE, mpp=CITY, point=point)
    ty, tx = math.floor(sy) + HEADER, math.floor(sx)
    assert tiled[ty, tx] == INK
    marked = draw_map_view(
        view=replace(view, markers=(live(at=(sx, sy)),)),
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
