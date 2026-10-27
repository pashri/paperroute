"""Facts about the drawn connection Strip + map (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import NullTiles
from paperroute.app_render import MapRenderer, drawing
from paperroute.core.connection_map import build_connection_map
from paperroute.core.connection_strip import (
    ConnectionStripMapView,
    build_connection_strip,
)
from paperroute.core.connection_view import build_connection_view
from paperroute.core.map_view import MAP_HEIGHT, STRIP_MAP_WIDTH
from paperroute.core.saved import ConnectionMap
from paperroute.render.canvas import HEIGHT, INK, PAPER
from paperroute.render.chrome import HEADER
from paperroute.render.connection_strip_map import draw_connection_strip_map
from tests.core.connection_factories import WORK, at
from tests.core.test_connection_map import SHAPED, inputs_at
from tests.core.test_connection_strip import CHAINS, PATTERNS, STOP

VIEW: Final[ConnectionStripMapView] = ConnectionStripMapView(
    map=build_connection_map(
        inputs=replace(inputs_at(now=at(7, 35)), width=STRIP_MAP_WIDTH),
        pin=WORK,
        chains=SHAPED,
        mode=ConnectionMap.CHANGES,
    ),
    pane=build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=0, patterns=PATTERNS,
    ),
)
BASE: Final[NDArray[np.uint8]] = np.full(
    (MAP_HEIGHT, STRIP_MAP_WIDTH), PAPER, dtype=np.uint8,
)


def test_frame_size_and_rule() -> None:
    """The frame is 480 by 800 with an ink rule right of the map."""
    frame = draw_connection_strip_map(view=VIEW, base=BASE)
    assert frame.shape == (480, 800)
    assert frame[HEADER + 2:HEIGHT, STRIP_MAP_WIDTH + 1].max() == INK


def test_sections_drawn_right_of_rule() -> None:
    """Both section headings draw as ink bands in the pane."""
    frame = draw_connection_strip_map(view=VIEW, base=BASE)
    pane = frame[HEADER:, STRIP_MAP_WIDTH + 20:780]
    bands = np.flatnonzero((pane == INK).mean(axis=1) > 0.6).tolist()
    starts = [y for y in bands if not set(range(y - 6, y)) & set(bands)]
    assert len(starts) == 2
    assert (frame[HEADER:, 790:] == PAPER).all()


def test_drawing_sends_view_to_map_path() -> None:
    """The app draws the view over the renderer's map base."""
    maps = MapRenderer(tiles=NullTiles())
    frame = drawing(view=VIEW, maps=maps)()
    assert frame.shape == (480, 800)
    assert maps.key is not None
    assert maps.key[0] == VIEW.map.base


def test_drawing_picks_connection_views() -> None:
    """The Stop view and Strip of a connection draw without a map."""
    maps = MapRenderer(tiles=NullTiles())
    assert drawing(view=VIEW.pane, maps=maps)().shape == (480, 800)
    stop = build_connection_view(
        stop_view=STOP, pin=WORK, chains=CHAINS, scroll=0, now=at(7, 30),
    )
    assert drawing(view=stop, maps=maps)().shape == (480, 800)
    assert maps.key is None
