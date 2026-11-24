"""Tests for app: tiles."""

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest

from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_testkit.core.kit_steps import app_of
from paperroute_testkit.fakes import STOP_POINT, BuiltTimetable
from paperroute_transit.adapters.tiles_db import (
    NullTiles,
    SqliteTileRepository,
)
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.build.map_style import LIGHT
from paperroute_transit.core.map_view import MapBase
from paperroute_transit.core.tiles import TILE, tile_of, to_pixels
from paperroute_transit.render.tile_codec import pack_grey
from tests.app.helpers import (
    PressingDisplay,
    VersionlessTiles,
    run_sydney,
    tableless_tiles,
)
from tests.app_effects.helpers import kit_ports
from tests.app_factories import MAP_MPP, MAP_PIN, map_view, ports_of


@dataclass
class ZoomingDisplay(PressingDisplay):
    """A display that shows the Map, then zooms in on the next frames."""

    def before_show(self) -> None:
        """Press the bottom knob, then turn it twice, as frames arrive."""
        super().before_show()
        if self.post is not None and 1 <= len(self.frames) <= 2:
            self.post(KnobTurned(knob=Knob.BOTTOM, steps=-1))


def test_tiles_read_error_draws_map_not_installed(tmp_path: Path) -> None:
    """A tiles file that fails a read draws as if no tiles were there."""
    view = map_view()
    with closing(tableless_tiles(out=tmp_path / 'tiles.db')) as tiles:
        broken = MapRenderer(tiles=tiles).frame(view=view)
    missing = MapRenderer(tiles=NullTiles()).frame(view=view)
    assert np.array_equal(broken, missing)


async def test_run_survives_a_tiles_read_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Map frames keep coming, with one warning, when tile reads fail."""
    display = ZoomingDisplay()
    tiles = tableless_tiles(out=tmp_path / 'tiles.db')
    ports = replace(ports_of(timetable=BuiltTimetable()), tiles=tiles)
    with caplog.at_level(logging.WARNING), closing(tiles):
        kit = await run_sydney(
            ports=ports,
            kit=kit_ports(display=display),
            seed=MAP_PIN,
            post_hook=display.hook,
            max_events=30,
        )
    assert kit.running
    assert app_of(kit=kit).map.step != 0
    assert len(display.frames) >= 4
    unusable = [
        r for r in caplog.records if r.message == 'tiles file unusable'
    ]
    assert len(unusable) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_tiles_version_error_draws_map_not_installed() -> None:
    """A tiles build that can't be read draws as if no tiles were there."""
    view = map_view()
    broken = MapRenderer(tiles=VersionlessTiles()).frame(view=view)
    missing = MapRenderer(tiles=NullTiles()).frame(view=view)
    assert np.array_equal(broken, missing)


def corrupt_tiles(*, out: Path, blob: bytes | None) -> SqliteTileRepository:
    """
    Write a tiles file whose tile under the test point is corrupt.

    Parameters
    ----------
    out : Path
        Destination file.
    blob : bytes | None
        The stored tile, or None for NULL.

    Returns
    -------
    SqliteTileRepository
        The opened file; it passes the open check.
    """
    col, row = tile_of(pixel=to_pixels(
        point=STOP_POINT,
        mpp=MAP_MPP,
        projection=SYDNEY_PROJECTION,
    ))
    with closing(sqlite3.connect(out)) as conn:
        with conn:
            conn.execute('CREATE TABLE meta (key TEXT, value TEXT)')
            conn.execute("INSERT INTO meta VALUES ('built_at', 'corrupt')")
            conn.execute(
                'CREATE TABLE tiles (zoom INTEGER, col INTEGER, row INTEGER,'
                ' grey BLOB, bits BLOB)'
            )
            conn.execute(
                'INSERT INTO tiles VALUES (?, ?, ?, ?, ?)',
                (MAP_MPP, col, row, blob, blob),
            )
    return SqliteTileRepository(path=out)


@pytest.mark.parametrize(
    'blob',
    [pack_grey(grey=np.full((TILE, TILE), LIGHT, dtype=np.uint8))[:10], None],
    ids=['truncated', 'null'],
)
def test_corrupt_tile_draws_map_not_installed_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, blob: bytes | None
) -> None:
    """A tile that can't be decoded draws as no tiles, warning once."""
    tiles = corrupt_tiles(out=tmp_path / 'tiles.db', blob=blob)
    maps = MapRenderer(tiles=tiles)
    with caplog.at_level(logging.WARNING), closing(tiles):
        first = maps.frame(view=map_view())
        moved = MapBase(
            centre=STOP_POINT,
            mpp=MAP_MPP,
            route=((STOP_POINT,),),
            projection=SYDNEY_PROJECTION,
        )
        maps.frame(view=replace(map_view(), base=moved))
    missing = MapRenderer(tiles=NullTiles()).frame(view=map_view())
    assert np.array_equal(first, missing)
    unusable = [
        r for r in caplog.records if r.message == 'tiles file unusable'
    ]
    assert len(unusable) == 1
