"""Draw a connection pin's Strip + map: the map beside the leg sections."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.connection_strip import ConnectionStripMapView
from paperroute.core.map_view import STRIP_MAP_WIDTH
from paperroute.render.chrome import BANNER, view_banner, view_header
from paperroute.render.connection_strip import RULE_WIDTH, draw_strip_sections
from paperroute.render.keep_out import NO_KEEP_OUT, KeepOut
from paperroute.render.map_view import draw_overlay, snap
from paperroute_device.render.canvas import HEIGHT, INK, Canvas
from paperroute_device.render.frame import HEADER

PANE_LEFT: Final[int] = STRIP_MAP_WIDTH + 16
PANE_RIGHT: Final[int] = 786


def draw_connection_strip_map(
    *,
    view: ConnectionStripMapView,
    base: NDArray[np.uint8],
    keep_out: KeepOut = NO_KEEP_OUT,
) -> NDArray[np.uint8]:
    """
    Draw a connection Strip + map frame over a cached map base.

    Parameters
    ----------
    view : ConnectionStripMapView
        View-model.
    base : NDArray[np.uint8]
        The map base, ``MAP_HEIGHT`` by ``STRIP_MAP_WIDTH``; never changed.
    keep_out : KeepOut
        Map-pixel boxes of the base's baked names and corner boxes.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels: four greys in the map, ink and paper elsewhere.
    """
    canvas = Canvas()
    canvas.image(grey=base, at=(0, HEADER))
    draw_overlay(canvas, view=view.map, keep_out=keep_out)
    rule = (STRIP_MAP_WIDTH, HEADER, STRIP_MAP_WIDTH + RULE_WIDTH, HEIGHT)
    canvas.rect(box=rule, grey=INK)
    draw_strip_sections(
        canvas,
        sections=view.pane.sections,
        left=PANE_LEFT,
        right=PANE_RIGHT,
        top=HEADER + (BANNER if view.map.banner else 0),
    )
    view_header(canvas, view=view.map, active='Strip + map')
    view_banner(canvas, view=view.map)
    return snap(grey=canvas.grey(), width=STRIP_MAP_WIDTH)
