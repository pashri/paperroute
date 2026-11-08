"""Draw a list of connection chains: leave, routes, detail and arrive."""

from collections.abc import Sequence
from typing import Final

from paperroute.render.chrome import (
    LINK_GAP,
    badge,
    link_arrow,
    row_px,
    shared_width,
)
from paperroute_device.render.canvas import INK, Canvas, Face
from paperroute_transit.core.connection_view import PairRow

INSET: Final[int] = 6
ROUTES_LEFT: Final[int] = 70  # from the list's left edge
ARRIVE_ROOM: Final[int] = 64
LEAVE_FACE: Final[Face] = Face(weight=800, px=24)
ARRIVE_FACE: Final[Face] = Face(weight=800, px=20)
DETAIL_FACE: Final[Face] = Face(weight=400, px=14)
CHIP_PX: Final[int] = 14
MIN_UNIT: Final[str] = 'min'


def route_chips(
    canvas: Canvas, *, x: float, y: float, chips: Sequence[str],
    room: float, px: int = CHIP_PX,
) -> float:
    """
    Draw route badges with a link between each.

    Parameters
    ----------
    canvas : Canvas
        Target.
    x : float
        Left edge.
    y : float
        Vertical centre.
    chips : Sequence[str]
        Route labels in order.
    room : float
        Width they must fit; the text shrinks to fit it, and past
        MIN_PAIR_PX the badges narrow.
    px : int
        Largest text size.

    Returns
    -------
    float
        Total width.
    """
    px = row_px(count=len(chips), px=px, room=room)
    each = shared_width(px=px, count=len(chips), room=room)
    width = 0.0
    for index, label in enumerate(chips):
        if index:
            width += LINK_GAP
            width += link_arrow(canvas, x=x + width, y=y, grey=INK)
            width += LINK_GAP
        width += badge(
            canvas, x=x + width, y=y, route=label, px=px, width=each,
        )
    return width


def leave_time(canvas: Canvas, *, row: PairRow, x: float, y: int) -> None:
    """
    Draw a row's leave time: a live count, or the clock time.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : PairRow
        The pair.
    x : float
        Left edge of the text.
    y : int
        Row top.
    """
    if not row.live:
        canvas.text(
            at=(x, y + 32), text=row.leaves_at, face=LEAVE_FACE, grey=INK,
        )
        return
    canvas.text(
        at=(x, y + 32), text=row.countdown, face=LEAVE_FACE, grey=INK,
    )
    if row.countdown.isdigit():
        canvas.text(
            at=(x, y + 50), text=MIN_UNIT, face=DETAIL_FACE, grey=INK,
        )


def draw_pair_row(
    canvas: Canvas,
    *,
    row: PairRow,
    y: int,
    edges: tuple[int, int],
    detail: str,
) -> None:
    """
    Draw one pair row below a rule.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : PairRow
        The pair.
    y : int
        Row top.
    edges : tuple[int, int]
        Left and right of the list.
    detail : str
        The line under the routes.
    """
    left, right = edges
    routes = left + ROUTES_LEFT
    room = right - ARRIVE_ROOM - routes
    canvas.rect(box=(left, y, right, y + 1), grey=INK)
    leave_time(canvas, row=row, x=left + INSET, y=y)
    route_chips(canvas, x=routes, y=y + 20, chips=row.chips, room=room)
    canvas.text(
        at=(routes, y + 50),
        text=canvas.fit(
            text=detail, face=DETAIL_FACE, width=right - INSET - routes,
        ),
        face=DETAIL_FACE,
        grey=INK,
    )
    canvas.text(
        at=(right - INSET, y + 32), text=row.arrives_at, face=ARRIVE_FACE,
        grey=INK, anchor='rs',
    )


def draw_pair_list(
    canvas: Canvas,
    *,
    rows: Sequence[PairRow],
    top: int,
    left: int,
    right: int,
    step: int,
    selected: int | None = None,
    first_detail: str | None = None,
) -> None:
    """
    Draw pair rows one under another.

    Parameters
    ----------
    canvas : Canvas
        Target.
    rows : Sequence[PairRow]
        The pairs, top first.
    top : int
        Top of the first row.
    left : int
        Left of the list.
    right : int
        Right of the list.
    step : int
        Row height.
    selected : int | None
        Index of a row drawn white on black, or None.
    first_detail : str | None
        Line drawn under the first row's routes in place of its detail,
        or None to keep it.
    """
    for index, row in enumerate(rows):
        y = top + index * step
        detail = row.detail
        if index == 0 and first_detail is not None:
            detail = first_detail
        draw_pair_row(
            canvas, row=row, y=y, edges=(left, right), detail=detail,
        )
        if index == selected:
            canvas.invert(box=(left, y, right, y + step))
