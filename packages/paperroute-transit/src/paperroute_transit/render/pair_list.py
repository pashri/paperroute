"""Draw a list of connection chains: leave, routes, detail and arrive."""

from collections.abc import Sequence
from typing import Final

from paperroute_device.core.text import DOT
from paperroute_device.render.canvas import (
    INK,
    Canvas,
    Face,
    Point,
    text_width,
)
from paperroute_transit.core.connection_late import TIGHT
from paperroute_transit.core.connection_view import PairRow
from paperroute_transit.core.stop_view import UNCERTAIN
from paperroute_transit.render.chrome import (
    LINK_GAP,
    badge,
    link_arrow,
    row_px,
    shared_width,
)
from paperroute_transit.render.late_marks import (
    SMALL_MARK,
    mark_width,
    raised_mark,
)

INSET: Final[int] = 6
ROUTES_LEFT: Final[int] = 70  # from the list's left edge
ARRIVE_ROOM: Final[int] = 64
LEAVE_FACE: Final[Face] = Face(weight=800, px=24)
ARRIVE_FACE: Final[Face] = Face(weight=800, px=20)
DETAIL_FACE: Final[Face] = Face(weight=400, px=14)
CHIP_PX: Final[int] = 14
MIN_UNIT: Final[str] = 'min'
ROUTES_CLEAR: Final[int] = 8  # pixels clear after a marked leave time
TIGHT_FACE: Final[Face] = Face(weight=700, px=14)
TIGHT_GAP: Final[int] = 6  # pixels after the word tight
STRUCK_FACE: Final[Face] = Face(weight=700, px=14)
STRUCK_GAP: Final[int] = 4  # pixels between a struck arrival and the new


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


def leave_text(*, row: PairRow) -> str:
    """
    Give a row's leave time as drawn.

    Parameters
    ----------
    row : PairRow
        The pair.

    Returns
    -------
    str
        The count when live, else the clock time.
    """
    return row.countdown if row.live else row.leaves_at


def routes_left(*, rows: Sequence[PairRow]) -> float:
    """
    Place the routes column clear of every row's marked leave time.

    Parameters
    ----------
    rows : Sequence[PairRow]
        The rows drawn.

    Returns
    -------
    float
        Offset from the list's left: `ROUTES_LEFT`, or more when a
        marked leave time and `ROUTES_CLEAR` after it need it.
    """
    marked = (
        INSET
        + text_width(text=leave_text(row=row), face=LEAVE_FACE)
        + mark_width(mark=row.mark, face=SMALL_MARK)
        + ROUTES_CLEAR
        for row in rows
        if row.mark
    )
    return max((ROUTES_LEFT, *marked))


def leave_time(canvas: Canvas, *, row: PairRow, x: float, y: int) -> None:
    """
    Draw a row's leave time: a live count, or the clock time, then its mark.

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
    used = canvas.text(
        at=(x, y + 32), text=leave_text(row=row), face=LEAVE_FACE, grey=INK,
    )
    raised_mark(
        canvas, mark=row.mark, at=(x + used, y + 32), face=LEAVE_FACE,
        mark_face=SMALL_MARK,
    )
    if row.live and row.countdown.isdigit():
        canvas.text(
            at=(x, y + 50), text=MIN_UNIT, face=DETAIL_FACE, grey=INK,
        )


def arrive_room(*, row: PairRow) -> float:
    """
    Measure the room kept for a row's arrival.

    Parameters
    ----------
    row : PairRow
        The pair.

    Returns
    -------
    float
        `ARRIVE_ROOM`, plus the struck arrival's width and its gap when
        the row has one.
    """
    if not row.was:
        return ARRIVE_ROOM
    return (
        ARRIVE_ROOM + text_width(text=row.was, face=STRUCK_FACE) + STRUCK_GAP
    )


def detail_line(
    canvas: Canvas, *, row: PairRow, detail: str, at: Point, width: float,
) -> None:
    """
    Draw the line under a row's routes, led by a bold tight when tight.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : PairRow
        The pair.
    detail : str
        The line, already led by any uncertain note.
    at : Point
        Left end of the line, on its baseline.
    width : float
        Room for the whole line.
    """
    x, baseline = at
    if row.tight:
        used = canvas.text(
            at=(x, baseline), text=TIGHT, face=TIGHT_FACE, grey=INK,
        ) + TIGHT_GAP
        x += used
        width -= used
    canvas.text(
        at=(x, baseline),
        text=canvas.fit(text=detail, face=DETAIL_FACE, width=width),
        face=DETAIL_FACE,
        grey=INK,
    )


def arrival(canvas: Canvas, *, row: PairRow, right: float, y: int) -> None:
    """
    Draw a row's arrival, after the struck arrival it replaced.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : PairRow
        The pair.
    right : float
        Right end of the arrival.
    y : int
        Row top.
    """
    used = canvas.text(
        at=(right, y + 32), text=row.arrives_at, face=ARRIVE_FACE,
        grey=INK, anchor='rs',
    )
    if not row.was:
        return
    end = right - used - STRUCK_GAP if row.arrives_at else right
    width = canvas.text(
        at=(end, y + 32), text=row.was, face=STRUCK_FACE, grey=INK,
        anchor='rs',
    )
    canvas.rect(box=(end - width, y + 27, end, y + 28), grey=INK)


def draw_pair_row(
    canvas: Canvas,
    *,
    row: PairRow,
    y: int,
    edges: tuple[int, int],
    detail: str,
    routes: float = ROUTES_LEFT,
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
    routes : float
        Where the routes column starts, from the list's left.
    """
    left, right = edges
    start = left + routes
    room = right - arrive_room(row=row) - start
    if row.uncertain:
        detail = f'{UNCERTAIN}{DOT}{detail}'
    canvas.rect(box=(left, y, right, y + 1), grey=INK)
    leave_time(canvas, row=row, x=left + INSET, y=y)
    route_chips(canvas, x=start, y=y + 20, chips=row.chips, room=room)
    detail_line(
        canvas, row=row, detail=detail, at=(start, y + 50),
        width=right - INSET - start,
    )
    arrival(canvas, row=row, right=right - INSET, y=y)


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
    routes = routes_left(rows=rows)
    for index, row in enumerate(rows):
        y = top + index * step
        detail = row.detail
        if index == 0 and first_detail is not None:
            detail = first_detail
        draw_pair_row(
            canvas, row=row, y=y, edges=(left, right), detail=detail,
            routes=routes,
        )
        if index == selected:
            canvas.invert(box=(left, y, right, y + step))
