"""Tests for app: map cache."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np

from paperroute_device.core.panel_views import MenuView
from paperroute_device.render.frame import HEADER
from paperroute_device.render.panel import draw_panel_view
from paperroute_device.render.sleep import with_sleep_band
from paperroute_device.runtime.outputs import drawing as kit_drawing
from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_testkit.fakes import LabelCountingTiles
from paperroute_transit.adapters.tiles_db import (
    NullTiles,
    SqliteTileRepository,
)
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_render import MapRenderer, drawing
from paperroute_transit.build.map_style import LIGHT
from paperroute_transit.build.tile_store import TileWriter
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import STRIP_MAP_WIDTH, Marker
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.tiles import TILE, ZOOMS, tile_of, to_pixels
from paperroute_transit.render.keep_out import KeepOut, keep_out
from paperroute_transit.render.map_corners import (
    NOT_INSTALLED,
    credit_box,
    scale_box,
)
from paperroute_transit.render.tile_codec import pack_bits, pack_grey
from tests.app.helpers import (
    BUS_POSITIONS,
    CountingTiles,
    PressingDisplay,
    RecordingFeeds,
    VersionlessTiles,
    _build_db,
    run_sydney,
    tableless_tiles,
)
from tests.app_effects.helpers import kit_ports, sydney_app
from tests.app_factories import (
    MAP_PIN,
    REAL,
    map_view,
    ports_of,
    strip_map_view,
)


def light_tiles(*, out: Path, point: Point) -> SqliteTileRepository:
    """
    Write a tiles file with a LIGHT tile under a point at each zoom.

    Parameters
    ----------
    out : Path
        Destination file.
    point : Point
        Point in metres.

    Returns
    -------
    SqliteTileRepository
        The opened file.
    """
    grey = np.full((TILE, TILE), LIGHT, dtype=np.uint8)
    with TileWriter(out=out) as writer:
        for mpp in ZOOMS:
            col, row = tile_of(pixel=to_pixels(
                point=point,
                mpp=mpp,
                projection=SYDNEY_PROJECTION,
            ))
            writer.add_tile(
                zoom=mpp,
                col=col,
                row=row,
                grey=pack_grey(grey=grey),
                bits=pack_bits(paper=grey >= 128),
            )
        writer.finish(meta={})
    return SqliteTileRepository(path=out)


async def test_run_draws_map_after_bottom_press(tmp_path: Path) -> None:
    """A bottom press shows the Map over the tiles and polls positions."""
    timetable = SqliteTimetableRepository(
        path=_build_db(tmp_path),
        city=SYDNEY,
    )
    place = timetable.stop(stop_id=MAP_PIN.origin.stop_id)
    assert place.point is not None
    tiles = light_tiles(out=tmp_path / 'tiles.db', point=place.point)
    display = PressingDisplay()
    feeds = RecordingFeeds(directory=REAL)
    ports = replace(ports_of(timetable=timetable), feeds=feeds, tiles=tiles)
    try:
        await run_sydney(
            ports=ports,
            kit=kit_ports(display=display),
            seed=MAP_PIN,
            post_hook=display.hook,
            max_events=80,
        )
    finally:
        timetable.close()
        tiles.close()
    assert LIGHT in display.frames[-1]
    assert BUS_POSITIONS in feeds.fetched


def test_map_base_is_cached_between_overlay_frames() -> None:
    """A second frame with the same base reads no more tiles."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.reads
    bus = Marker(
        route='606', x=200.0, y=200.0, bearing=90.0, live=True, pinned=True
    )
    maps.frame(view=map_view(markers=(bus,)))
    assert once > 0
    assert tiles.reads == once


def test_renderer_caches_boxes_with_the_base() -> None:
    """A second frame with the same base reads no more labels."""
    tiles = LabelCountingTiles(tiles=CountingTiles())
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.label_reads
    bus = Marker(
        route='606', x=200.0, y=200.0, bearing=90.0, live=True, pinned=True
    )
    maps.frame(view=map_view(markers=(bus,)))
    assert once > 0
    assert tiles.label_reads == once
    assert maps.boxes(base=map_view().base) is maps.boxes(
        base=map_view().base,
    )


def test_unreadable_tiles_give_corner_boxes_only(tmp_path: Path) -> None:
    """Tiles that fail a read keep out only the corner boxes, as drawn."""
    base = map_view().base
    with closing(tableless_tiles(out=tmp_path / 'tiles.db')) as tiles:
        broken = MapRenderer(tiles=tiles).boxes(base=base)
    versionless = MapRenderer(tiles=VersionlessTiles()).boxes(base=base)
    edged = tuple(
        (left - 1, top - 1, right + 1, bottom + 1)
        for left, top, right, bottom in (
            scale_box(mpp=base.mpp),
            credit_box(width=base.width, text=NOT_INSTALLED),
        )
    )
    assert broken == KeepOut(places=edged, streets=(), corners=edged)
    assert versionless == broken
    assert broken == keep_out(base=base, tiles=NullTiles())


def test_drawing_picks_panel_and_sleep() -> None:
    """The kit draws panel views; transit bands a plain map for sleep."""
    maps = MapRenderer(tiles=CountingTiles())
    menu = MenuView(clock='9:00', rows=('Wi-Fi',), cursor=0, first=0)
    panel = kit_drawing(app=sydney_app(ports=ports_of()), view=menu)()
    assert (panel == draw_panel_view(view=menu)).all()
    asleep = drawing(
        view=SleepView(map=map_view(), band='Asleep'),
        maps=maps,
    )()
    banded = with_sleep_band(frame=maps.frame(view=map_view()), band='Asleep')
    assert (asleep == banded).all()


def test_tiles_replaced_rebuilds_base() -> None:
    """A new tiles build composes the base again."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    once = tiles.reads
    tiles.built = 'v2'
    maps.frame(view=map_view())
    assert tiles.reads == 2 * once


def test_strip_map_reuses_base_cache() -> None:
    """A second Strip + map frame reads no more tiles than the first."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    first = maps.strip_map_frame(view=strip_map_view())
    once = tiles.reads
    bus = Marker(
        route='606', x=200.0, y=200.0, bearing=90.0, live=True, pinned=True
    )
    maps.strip_map_frame(view=strip_map_view(markers=(bus,)))
    assert once > 0
    assert tiles.reads == once
    assert first.shape == (480, 800)
    assert (first[HEADER:, :STRIP_MAP_WIDTH] == LIGHT).any()


def test_strip_map_and_map_keep_separate_bases() -> None:
    """Flipping between Map and Strip + map draws each width's base."""
    tiles = CountingTiles()
    maps = MapRenderer(tiles=tiles)
    maps.frame(view=map_view())
    wide = tiles.reads
    maps.strip_map_frame(view=strip_map_view())
    assert tiles.reads > wide
