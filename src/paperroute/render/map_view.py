"""Draw the Map view: header, map with markers, sidebar and banner."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapView,
    screen_point,
)
from paperroute.core.stop_view import Row
from paperroute.render.canvas import HEIGHT, INK, PAPER, Box, Canvas, Face
from paperroute.render.chrome import HEADER, badge, banner, header
from paperroute.render.map_base import HALO, draw_stop
from paperroute.render.markers import draw_markers
from paperroute.render.tile_codec import THRESHOLD, quantise4

SIDEBAR_LEFT: Final[int] = 520
RULE_WIDTH: Final[int] = 3
SIDEBAR_TEXT: Final[int] = 538
SIDEBAR_RIGHT: Final[int] = 784
MAP_BOX: Final[Box] = (0, HEADER, SIDEBAR_LEFT, HEIGHT)
EMPTY_FACE: Final[Face] = Face(weight=700, px=22)
EMPTY_MARGIN: Final[int] = 32  # pixels each side of the empty text
DESTINATION_FACE: Final[Face] = Face(weight=700, px=16)
DESTINATION_Y: Final[int] = 70
DESTINATION_WIDTH: Final[int] = 250
ROWS_TOP: Final[int] = 110
ROW_STEP: Final[int] = 72
BADGE_PX: Final[int] = 17
COUNTDOWN_FACE: Final[Face] = Face(weight=800, px=34)
COUNTDOWN_DROP: Final[int] = 12  # baseline below the badge centre
ARRIVES_FACE: Final[Face] = Face(weight=400, px=14)
ARRIVES_DROP: Final[int] = 26  # baseline below the badge centre
ROW_RULE_DROP: Final[int] = 40  # below the badge centre
ROW_RULE_LEFT: Final[int] = 534
ROW_RULE_RIGHT: Final[int] = 788


def draw_overlay(canvas: Canvas, *, view: MapView) -> None:
    """
    Draw the empty text, the markers and the From stop over the map.

    The stop is drawn again after the markers so no badge buries it.

    Parameters
    ----------
    canvas : Canvas
        Target, with the base already drawn below the header.
    view : MapView
        View-model.
    """
    if view.empty:
        canvas.text(
            at=(MAP_WIDTH / 2, HEADER + MAP_HEIGHT / 2),
            text=canvas.fit(
                text=view.empty,
                face=EMPTY_FACE,
                width=MAP_WIDTH - 2 * EMPTY_MARGIN,
            ),
            face=EMPTY_FACE,
            grey=INK,
            anchor='mm',
            halo=HALO,
        )
    centre = view.base.centre
    with canvas.clipped(box=MAP_BOX):
        draw_markers(
            canvas,
            markers=view.markers,
            mpp=view.base.mpp,
            origin=(0, HEADER),
        )
        if centre is not None:
            x, y = screen_point(centre=centre, mpp=view.base.mpp, point=centre)
            draw_stop(canvas, at=(x, y + HEADER))


def sidebar_row(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw one sidebar departure.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        Departure.
    y : int
        The badge's vertical centre.
    """
    badge(canvas, x=SIDEBAR_TEXT, y=y, route=row.route, px=BADGE_PX)
    canvas.text(
        at=(SIDEBAR_RIGHT, y + COUNTDOWN_DROP),
        text=row.countdown,
        face=COUNTDOWN_FACE,
        grey=INK,
        anchor='rs',
    )
    canvas.text(
        at=(SIDEBAR_TEXT, y + ARRIVES_DROP),
        text=f'arr {row.arrives_at}',
        face=ARRIVES_FACE,
        grey=INK,
    )
    canvas.rect(
        box=(
            ROW_RULE_LEFT,
            y + ROW_RULE_DROP,
            ROW_RULE_RIGHT,
            y + ROW_RULE_DROP + 1,
        ),
        grey=INK,
    )


def draw_sidebar(canvas: Canvas, *, view: MapView) -> None:
    """
    Draw the rule, the destination and the next departures.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : MapView
        View-model.
    """
    canvas.rect(
        box=(SIDEBAR_LEFT, HEADER, SIDEBAR_LEFT + RULE_WIDTH, HEIGHT),
        grey=INK,
    )
    canvas.text(
        at=(SIDEBAR_TEXT, DESTINATION_Y),
        text=canvas.fit(
            text=view.destination,
            face=DESTINATION_FACE,
            width=DESTINATION_WIDTH,
        ),
        face=DESTINATION_FACE,
        grey=INK,
    )
    for index, row in enumerate(view.sidebar):
        sidebar_row(canvas, row=row, y=ROWS_TOP + index * ROW_STEP)


def snap(*, grey: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """
    Keep four greys in the map and only ink and paper elsewhere.

    Parameters
    ----------
    grey : NDArray[np.uint8]
        The drawn frame.

    Returns
    -------
    NDArray[np.uint8]
        The finished frame.
    """
    out = np.where(grey < THRESHOLD, INK, PAPER).astype(np.uint8)
    out[HEADER:, :SIDEBAR_LEFT] = quantise4(grey=grey[HEADER:, :SIDEBAR_LEFT])
    return out


def draw_map_view(
    *,
    view: MapView,
    base: NDArray[np.uint8],
) -> NDArray[np.uint8]:
    """
    Draw a Map view frame over a cached map base.

    Parameters
    ----------
    view : MapView
        View-model.
    base : NDArray[np.uint8]
        The map base, ``MAP_HEIGHT`` by ``MAP_WIDTH``; never changed.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels: four greys in the map, ink and paper elsewhere.
    """
    canvas = Canvas()
    canvas.image(grey=base, at=(0, HEADER))
    draw_overlay(canvas, view=view)
    header(
        canvas,
        active='Map',
        clock=view.clock,
        origin=view.origin,
        destination=view.destination,
    )
    draw_sidebar(canvas, view=view)
    if view.banner:
        banner(canvas, text=view.banner)
    return snap(grey=canvas.grey())
