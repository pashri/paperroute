"""Draw a list of connection pairs: leave, routes, detail and arrive."""

from collections.abc import Sequence
from typing import Final

from paperroute.core.connection_view import PairRow
from paperroute.render.canvas import INK, Canvas, Face
from paperroute.render.chrome import route_pair

INSET: Final[int] = 6
ROUTES_LEFT: Final[int] = 70  # from the list's left edge
ARRIVE_ROOM: Final[int] = 64
LEAVE_FACE: Final[Face] = Face(weight=800, px=24)
ARRIVE_FACE: Final[Face] = Face(weight=800, px=20)
DETAIL_FACE: Final[Face] = Face(weight=400, px=14)


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
    canvas.text(
        at=(left + INSET, y + 32), text=row.leaves_at, face=LEAVE_FACE,
        grey=INK,
    )
    route_pair(
        canvas, x=routes, y=y + 20, route=row.route, onto=row.onto, px=14,
        room=room,
    )
    canvas.text(
        at=(routes, y + 50),
        text=canvas.fit(text=detail, face=DETAIL_FACE, width=room),
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
