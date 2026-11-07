"""Draw map panes side by side across the frame."""

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.map_view import MapView
from paperroute.render.chrome import view_banner, view_header
from paperroute.render.keep_out import NO_KEEP_OUT, KeepOut
from paperroute.render.map_view import draw_overlay, snap
from paperroute_device.render.canvas import HEIGHT, INK, WIDTH, Canvas
from paperroute_device.render.frame import HEADER

RULE_WIDTH: Final[int] = 3


def pane_pixels(
    *,
    view: MapView,
    base: NDArray[np.uint8],
    keep_out: KeepOut = NO_KEEP_OUT,
) -> NDArray[np.uint8]:
    """
    Draw one pane's map and overlay alone.

    Parameters
    ----------
    view : MapView
        The pane.
    base : NDArray[np.uint8]
        Its map base, ``MAP_HEIGHT`` by ``view.base.width``.
    keep_out : KeepOut
        Map-pixel boxes of the base's baked names and corner boxes.

    Returns
    -------
    NDArray[np.uint8]
        The pane below the header, ``view.base.width`` wide.
    """
    canvas = Canvas()
    canvas.image(grey=base, at=(0, HEADER))
    draw_overlay(canvas, view=view, keep_out=keep_out)
    return canvas.grey()[HEADER:, : view.base.width]


def draw_map_panes(
    *,
    view: MapView,
    bases: Sequence[NDArray[np.uint8]],
    keep_outs: Sequence[KeepOut] = (),
) -> NDArray[np.uint8]:
    """
    Draw a map and its panes side by side, under one header.

    Parameters
    ----------
    view : MapView
        The first pane, holding the others in `panes`.
    bases : Sequence[NDArray[np.uint8]]
        Each pane's map base, the first pane's first.
    keep_outs : Sequence[KeepOut]
        Each pane's keep-out boxes in the same order; none keeps nothing
        out.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels: four greys in the maps, ink and paper
        elsewhere, with an ink rule between panes.
    """
    canvas = Canvas()
    left = 0
    panes = (view, *view.panes)
    boxes = keep_outs or [NO_KEEP_OUT] * len(panes)
    for pane, base, keep_out in zip(panes, bases, boxes, strict=True):
        canvas.image(
            grey=pane_pixels(view=pane, base=base, keep_out=keep_out),
            at=(left, HEADER),
        )
        if left:
            canvas.rect(
                box=(left - 1, HEADER, left + RULE_WIDTH - 1, HEIGHT),
                grey=INK,
            )
        left += pane.base.width
    view_header(canvas, view=view, active='Map')
    view_banner(canvas, view=view)
    return snap(grey=canvas.grey(), width=WIDTH)
