"""Facts about map panes drawn side by side (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np

from paperroute.adapters.tiles_db import NullTiles
from paperroute.app_render import MapRenderer, drawing
from paperroute.core.connection_map import change_panes
from paperroute.core.map_view import MAP_HEIGHT, MapView
from paperroute.core.saved import ConnectionMap
from paperroute.render.map_panes import draw_map_panes
from paperroute_device.render.canvas import HEIGHT, INK, PAPER
from paperroute_device.render.frame import HEADER
from tests.core.test_connection_map import TWO, TWO_CHAIN, two_inputs

PANES: Final[tuple[MapView, ...]] = change_panes(
    inputs=two_inputs(), pin=TWO, chain=TWO_CHAIN, mode=ConnectionMap.CHANGES,
)
VIEW: Final[MapView] = replace(PANES[0], panes=PANES[1:])


def test_panes_meet_at_a_rule() -> None:
    """Two panes fill the frame with an ink rule between them."""
    paper = np.full((MAP_HEIGHT, 400), PAPER, dtype=np.uint8)
    frame = draw_map_panes(view=VIEW, bases=(paper, paper))
    assert frame.shape == (480, 800)
    assert (frame[HEADER + 2:HEIGHT, 400] == INK).all()


def test_renderer_draws_both_bases() -> None:
    """The renderer keeps the first base and a side base."""
    maps = MapRenderer(tiles=NullTiles())
    frame = drawing(view=VIEW, maps=maps)()
    assert frame.shape == (480, 800)
    assert maps.key is not None
    assert maps.key[0] == VIEW.base
    assert [key[0] for key in maps.side] == [PANES[1].base]
    drawing(view=VIEW, maps=maps)()
    assert len(maps.side) == 1
