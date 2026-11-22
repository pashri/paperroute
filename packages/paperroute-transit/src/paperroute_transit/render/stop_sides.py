"""The Stop view that shows the next bus from each kerb of a road."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import HEIGHT, INK, Canvas, Face
from paperroute_device.render.frame import HEADER
from paperroute_transit.core.stop_sides import NOTHING_THIS_SIDE
from paperroute_transit.core.stop_under import EachWay, Kerb
from paperroute_transit.core.stop_view import Row, StopView
from paperroute_transit.render.chrome import (
    BANNER,
    route_pair,
    view_banner,
    view_header,
)
from paperroute_transit.render.names import Naming, fit_headsign, fit_name
from paperroute_transit.render.stop_view import (
    OPEN_RIGHT,
    SMALL_FACE,
    SPLIT,
    draw_stop_view,
    finish_view,
    small_parts,
    small_text,
)

KERB_STEP: Final[int] = 206  # px between the two kerbs' tops
LABEL_FACE: Final[Face] = Face(weight=700, px=12)
HEADSIGN_FACE: Final[Face] = Face(weight=700, px=18)
COUNT_FACE: Final[Face] = Face(weight=800, px=76)
MIN_FACE: Final[Face] = Face(weight=800, px=24)
HEAD_FACE: Final[Face] = Face(weight=700, px=11)
LEAVES: Final[str] = 'LEAVES'
LEFT: Final[int] = 18  # px


def kerb_label(
    canvas: Canvas, *, kerb: Kerb[Row], y: int, naming: Naming
) -> None:
    """
    Draw the words that head a kerb's block, then its end.

    Parameters
    ----------
    canvas : Canvas
        Target.
    kerb : Kerb[Row]
        The kerb.
    y : int
        Top of the kerb's block.
    naming : Naming
        How names are shortened.
    """
    used = canvas.text(
        at=(LEFT, y + 22), text=kerb.label, face=LABEL_FACE, grey=INK,
    )
    if kerb.end:
        left = LEFT + used + 6
        end = fit_name(
            text=kerb.end,
            face=LABEL_FACE,
            width=SPLIT - LEFT - left,
            naming=naming,
        )[0]
        canvas.text(at=(left, y + 22), text=end, face=LABEL_FACE, grey=INK)


def kerb_count(canvas: Canvas, *, row: Row, y: int) -> None:
    """
    Draw a kerb's countdown or clock time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Row
        The kerb's next bus.
    y : int
        Top of the kerb's block.
    """
    shown = row.countdown if row.live else row.leaves_at
    used = canvas.text(
        at=(LEFT, y + 130), text=shown, face=COUNT_FACE, grey=INK,
    )
    if row.live and shown != 'now' and ':' not in shown:
        canvas.text(
            at=(LEFT + used + 8, y + 130), text='min', face=MIN_FACE,
            grey=INK,
        )


def kerb_bus(
    canvas: Canvas, *, kerb: Kerb[Row], y: int, naming: Naming
) -> None:
    """
    Draw a kerb's next bus, or that none comes.

    Parameters
    ----------
    canvas : Canvas
        Target.
    kerb : Kerb[Row]
        The kerb.
    y : int
        Top of the kerb's block.
    naming : Naming
        How names are shortened.
    """
    row, under = kerb.hero, kerb.under
    if row is None or under is None:
        canvas.text(
            at=(LEFT, y + 53), text=NOTHING_THIS_SIDE, face=HEADSIGN_FACE,
            grey=INK,
        )
        return
    width = route_pair(
        canvas, x=LEFT, y=y + 46, route=row.route, onto=row.onto, px=20,
    )
    headsign = fit_headsign(
        text=row.destination,
        face=HEADSIGN_FACE,
        width=SPLIT - 50 - width,
        naming=naming,
    )[0]
    canvas.text(
        at=(30 + width, y + 53), text=headsign, face=HEADSIGN_FACE,
        grey=INK,
    )
    kerb_count(canvas, row=row, y=y)
    small = small_text(
        parts=(under.status, *small_parts(under=under, row=row)),
        face=SMALL_FACE,
        width=400,
    )
    canvas.text(at=(LEFT, y + 160), text=small, face=SMALL_FACE, grey=INK)


def draw_each_way(
    canvas: Canvas, *, each_way: EachWay[Row], top: int, naming: Naming
) -> None:
    """
    Draw both kerbs' blocks, the rules between them and the list head.

    Parameters
    ----------
    canvas : Canvas
        Target.
    each_way : EachWay[Row]
        The two kerbs.
    top : int
        Top of the content below the header and any banner.
    naming : Naming
        How names are shortened.
    """
    lower = top + KERB_STEP
    for kerb, y in ((each_way.here, top), (each_way.across, lower)):
        kerb_label(canvas, kerb=kerb, y=y, naming=naming)
        kerb_bus(canvas, kerb=kerb, y=y, naming=naming)
    canvas.rect(box=(LEFT, lower - 8, 418, lower - 6), grey=INK)
    canvas.rect(box=(SPLIT, top, SPLIT + 3, HEIGHT), grey=INK)
    canvas.text(
        at=(OPEN_RIGHT, top + 22), text=LEAVES, face=HEAD_FACE, grey=INK,
        anchor='rs',
    )


def draw_each_way_view(*, view: StopView) -> NDArray[np.uint8]:
    """
    Draw a Stop view with the next bus from each kerb as a 1-bit frame.

    Parameters
    ----------
    view : StopView
        View-model; one without kerbs draws as the plain Stop view.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    each_way = view.each_way
    if each_way is None:
        return draw_stop_view(view=view)
    canvas = Canvas()
    view_header(canvas, view=view, active='Stop')
    top = HEADER + (BANNER if view.banner else 0)
    view_banner(canvas, view=view)
    heroes = (each_way.here.hero, each_way.across.hero)
    names = [item.destination for item in view.rows]
    names.extend(row.destination for row in heroes if row is not None)
    naming = Naming(rules=view.name_rules, siblings=tuple(names))
    draw_each_way(canvas, each_way=each_way, top=top, naming=naming)
    return finish_view(canvas, view=view, top=top, naming=naming)
