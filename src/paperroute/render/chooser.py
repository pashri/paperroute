"""Draw the route chooser frames in ink and paper."""

from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from paperroute.core.chooser import Choice, Level
from paperroute.core.chooser_view import (
    CHOOSER_BOTTOM,
    CHOOSER_TOP,
    ROW_HEIGHTS,
    ChooserView,
    ColumnView,
)
from paperroute.render.canvas import HEIGHT, INK, PAPER, WIDTH, Canvas, Face
from paperroute.render.stop_view import finish

COLUMN_WIDTHS: Final[dict[Level, int]] = {
    Level.KIND: 170,
    Level.GROUP: 125,
    Level.LINE: 260,
    Level.ROUTE: 330,
    Level.TOWARDS: 366,
}
RULE_WIDTH: Final[int] = 2
PAD: Final[int] = 10
TITLE_FACE: Final[Face] = Face(weight=700, px=15)
TRAIL_FACE: Final[Face] = Face(weight=800, px=18)
HEADER_BASELINE: Final[int] = 26
KIND_BOX: Final[int] = 22
KIND_NAME_FACE: Final[Face] = Face(weight=700, px=17)
SMALL_FACE: Final[Face] = Face(weight=400, px=12)
GROUP_FACE: Final[Face] = Face(weight=800, px=19)
NAME_FACE: Final[Face] = Face(weight=400, px=15)
COUNT_FACE: Final[Face] = Face(weight=700, px=13)
TAG_FACE: Final[Face] = Face(weight=700, px=12)
CHIP_FACE: Final[Face] = Face(weight=800, px=15)
LETTER_FACE: Final[Face] = Face(weight=800, px=14)
ROUTE_BADGE: Final[int] = 60
LINE_BADGE: Final[int] = 44
CHIP_HEIGHT: Final[int] = 22
TAG_HEIGHT: Final[int] = 18
TAG_PAD: Final[int] = 12
GAP: Final[int] = 8
THUMB_WIDTH: Final[int] = 4
THUMB_MIN: Final[int] = 20
NOTE_FACE: Final[Face] = Face(weight=700, px=15)
NOTE_Y: Final[int] = 200
EMPTY_FACE: Final[Face] = Face(weight=700, px=20)
EMPTY_Y: Final[int] = 260
VISIBLE_HEIGHT: Final[int] = CHOOSER_BOTTOM - CHOOSER_TOP
DOT_DASH: Final[tuple[float, float]] = (2, 3)

Rect = tuple[int, int, int, int]


def column_edges(*, columns: tuple[ColumnView, ...]) -> list[tuple[int, int]]:
    """
    Place the columns across the frame.

    Parameters
    ----------
    columns : tuple[ColumnView, ...]
        Columns, left to right.

    Returns
    -------
    list[tuple[int, int]]
        Left and right edge of each column; the last runs to the right
        edge of the frame.
    """
    edges: list[tuple[int, int]] = []
    left = 0
    for index, view in enumerate(columns):
        last = index == len(columns) - 1
        width = 0 if view.level is None else COLUMN_WIDTHS[view.level]
        right = WIDTH if last else left + width
        edges.append((left, right))
        left = right
    return edges


def ink_for(*, inverted: bool) -> tuple[int, int]:
    """
    Pick the foreground and background for a row.

    Parameters
    ----------
    inverted : bool
        Whether the row is a focused selection.

    Returns
    -------
    tuple[int, int]
        Text level and fill level.
    """
    return (PAPER, INK) if inverted else (INK, PAPER)


def chip(
    canvas: Canvas,
    *,
    box: Rect,
    text: str,
    face: Face,
    inverted: bool,
) -> None:
    """
    Draw a fixed-width label as an ink block, or a paper one when inverted.

    Parameters
    ----------
    canvas : Canvas
        Target.
    box : Rect
        Left, top, right, bottom.
    text : str
        Label.
    face : Face
        Font.
    inverted : bool
        Whether the row is a focused selection.
    """
    fill, text_grey = (PAPER, INK) if inverted else (INK, PAPER)
    canvas.rect(box=box, grey=fill)
    shown = canvas.fit(text=text, face=face, width=box[2] - box[0] - 4)
    canvas.text(
        at=((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 + 1),
        text=shown,
        face=face,
        grey=text_grey,
        anchor='mm',
    )


def draw_kind(canvas: Canvas, *, row: Choice, box: Rect, inverted: bool,
              ) -> None:
    """
    Draw a kind row: a letter box, the name and a detail line.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Choice
        The row.
    box : Rect
        The row's box.
    inverted : bool
        Whether the row is a focused selection.
    """
    left, top, right, _ = box
    grey, _ = ink_for(inverted=inverted)
    chip(
        canvas,
        box=(left + PAD, top + 17, left + PAD + KIND_BOX, top + 17 + KIND_BOX),
        text=row.badge,
        face=LETTER_FACE,
        inverted=inverted,
    )
    x = left + PAD + KIND_BOX + GAP
    name = canvas.fit(text=row.label, face=KIND_NAME_FACE, width=right - x)
    canvas.text(at=(x, top + 25), text=name, face=KIND_NAME_FACE, grey=grey)
    detail = canvas.fit(text=row.detail, face=SMALL_FACE, width=right - x)
    canvas.text(at=(x, top + 44), text=detail, face=SMALL_FACE, grey=grey)


def draw_group(canvas: Canvas, *, row: Choice, box: Rect, inverted: bool,
               ) -> None:
    """
    Draw a group row: its label and, right-aligned, its count.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Choice
        The row.
    box : Rect
        The row's box.
    inverted : bool
        Whether the row is a focused selection.
    """
    left, top, right, _ = box
    grey, _ = ink_for(inverted=inverted)
    count = canvas.text(
        at=(right - PAD, top + 21), text=row.detail, face=SMALL_FACE,
        grey=grey, anchor='rs',
    )
    label = canvas.fit(
        text=row.label, face=GROUP_FACE, width=right - left - 3 * PAD - count,
    )
    canvas.text(
        at=(left + PAD, top + 22), text=label, face=GROUP_FACE, grey=grey,
    )


def outlined_tag(
    canvas: Canvas,
    *,
    text: str,
    right: int,
    top: int,
    inverted: bool,
) -> int:
    """
    Draw a short outlined tag whose right edge is fixed.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        Tag text.
    right : int
        Right edge.
    top : int
        Top edge.
    inverted : bool
        Whether the row is a focused selection.

    Returns
    -------
    int
        The tag's left edge.
    """
    grey, fill = ink_for(inverted=inverted)
    left = int(right - canvas.length(text=text, face=TAG_FACE) - TAG_PAD)
    canvas.rect(box=(left, top, right, top + TAG_HEIGHT), grey=grey)
    canvas.rect(
        box=(left + 1, top + 1, right - 1, top + TAG_HEIGHT - 1), grey=fill,
    )
    canvas.text(
        at=((left + right) / 2, top + TAG_HEIGHT / 2 + 1),
        text=text,
        face=TAG_FACE,
        grey=grey,
        anchor='mm',
    )
    return left


def draw_route(canvas: Canvas, *, row: Choice, box: Rect, inverted: bool,
               ) -> None:
    """
    Draw a route row: a number badge, the fitted name and an outlined tag.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Choice
        The row.
    box : Rect
        The row's box.
    inverted : bool
        Whether the row is a focused selection.
    """
    left, top, right, bottom = box
    grey, _ = ink_for(inverted=inverted)
    middle = (top + bottom) // 2
    chip(
        canvas,
        box=(
            left + PAD,
            middle - CHIP_HEIGHT // 2,
            left + PAD + ROUTE_BADGE,
            middle + CHIP_HEIGHT // 2,
        ),
        text=row.badge,
        face=CHIP_FACE,
        inverted=inverted,
    )
    limit = right - PAD
    if row.tag:
        limit = outlined_tag(
            canvas, text=row.tag, right=right - PAD, top=middle - 9,
            inverted=inverted,
        ) - GAP
    x = left + PAD + ROUTE_BADGE + GAP
    name = canvas.fit(text=row.label, face=NAME_FACE, width=limit - x)
    canvas.text(at=(x, middle + 5), text=name, face=NAME_FACE, grey=grey)


def draw_line(canvas: Canvas, *, row: Choice, box: Rect, inverted: bool,
              ) -> None:
    """
    Draw a line row: a badge and the name, under a dotted rule if asked.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Choice
        The row.
    box : Rect
        The row's box.
    inverted : bool
        Whether the row is a focused selection.
    """
    left, top, right, bottom = box
    grey, _ = ink_for(inverted=inverted)
    middle = (top + bottom) // 2
    if row.rule_above:
        canvas.line(
            points=[(left + PAD, top + 0.5), (right - PAD, top + 0.5)],
            width=1,
            grey=INK,
            dash=DOT_DASH,
        )
    chip(
        canvas,
        box=(
            left + PAD,
            middle - CHIP_HEIGHT // 2,
            left + PAD + LINE_BADGE,
            middle + CHIP_HEIGHT // 2,
        ),
        text=row.badge,
        face=CHIP_FACE,
        inverted=inverted,
    )
    x = left + PAD + LINE_BADGE + GAP
    name = canvas.fit(text=row.label, face=NAME_FACE, width=right - PAD - x)
    canvas.text(at=(x, middle + 5), text=name, face=NAME_FACE, grey=grey)


def draw_towards(canvas: Canvas, *, row: Choice, box: Rect, inverted: bool,
                 ) -> None:
    """
    Draw a towards row: the name and, right-aligned, its count.

    Parameters
    ----------
    canvas : Canvas
        Target.
    row : Choice
        The row.
    box : Rect
        The row's box.
    inverted : bool
        Whether the row is a focused selection.
    """
    left, top, right, bottom = box
    grey, _ = ink_for(inverted=inverted)
    middle = (top + bottom) // 2
    count = canvas.text(
        at=(right - PAD - 4, middle + 5), text=row.detail, face=COUNT_FACE,
        grey=grey, anchor='rs',
    )
    name = canvas.fit(
        text=row.label, face=NAME_FACE, width=right - left - 3 * PAD - count,
    )
    canvas.text(
        at=(left + PAD + 2, middle + 5), text=name, face=NAME_FACE, grey=grey,
    )


class RowDrawer(Protocol):
    """Draws one row of a column."""

    def __call__(
        self,
        canvas: Canvas,
        *,
        row: Choice,
        box: Rect,
        inverted: bool,
    ) -> None:
        """
        Draw the row inside its box.

        Parameters
        ----------
        canvas : Canvas
            Target.
        row : Choice
            The row.
        box : Rect
            The row's box.
        inverted : bool
            Whether the row is a focused selection.
        """


DRAWERS: Final[dict[Level, RowDrawer]] = {
    Level.KIND: draw_kind,
    Level.GROUP: draw_group,
    Level.ROUTE: draw_route,
    Level.LINE: draw_line,
    Level.TOWARDS: draw_towards,
}


def mark_selection(canvas: Canvas, *, box: Rect, focused: bool) -> None:
    """
    Fill a focused selection with ink, or outline an unfocused one.

    Parameters
    ----------
    canvas : Canvas
        Target.
    box : Rect
        The row's box.
    focused : bool
        Whether the row's column has the knob.
    """
    left, top, right, bottom = box
    canvas.rect(box=box, grey=INK)
    if not focused:
        canvas.rect(
            box=(
                left + RULE_WIDTH,
                top + RULE_WIDTH,
                right - RULE_WIDTH,
                bottom - RULE_WIDTH,
            ),
            grey=PAPER,
        )


def scroll_bar(canvas: Canvas, *, view: ColumnView) -> None:
    """
    Draw an ink thumb at the right edge when the rows overflow.

    Where it crosses a focused selection it is drawn in paper.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : ColumnView
        The column.
    """
    if view.level is None:
        return
    fits = VISIBLE_HEIGHT // ROW_HEIGHTS[view.level]
    if view.total <= fits:
        return
    track = CHOOSER_BOTTOM - CHOOSER_TOP
    size = max(THUMB_MIN, track * fits // view.total)
    top = CHOOSER_TOP + (track - size) * view.first // (view.total - fits)
    left = WIDTH - THUMB_WIDTH
    canvas.rect(box=(left, top, WIDTH, top + size), grey=INK)
    if not view.focused:
        return
    height = ROW_HEIGHTS[view.level]
    row_top = CHOOSER_TOP + view.selected * height
    cut = (max(top, row_top), min(top + size, row_top + height))
    if cut[0] < cut[1]:
        canvas.rect(box=(left, cut[0], WIDTH, cut[1]), grey=PAPER)


def draw_note(canvas: Canvas, *, text: str, edges: tuple[int, int]) -> None:
    """
    Draw a column's note centred in it.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The note.
    edges : tuple[int, int]
        The column's left and right edges.
    """
    left, right = edges
    canvas.text(
        at=((left + right) / 2, NOTE_Y),
        text=canvas.fit(text=text, face=NOTE_FACE, width=right - left - 24),
        face=NOTE_FACE,
        grey=INK,
        anchor='mm',
    )


def draw_rows(
    canvas: Canvas,
    *,
    view: ColumnView,
    level: Level,
    left: int,
    right: int,
) -> None:
    """
    Draw a column's rows and mark its selection.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : ColumnView
        The column.
    level : Level
        What it lists.
    left : int
        Left edge.
    right : int
        Right edge, short of the rule.
    """
    height = ROW_HEIGHTS[level]
    for index, row in enumerate(view.items):
        top = CHOOSER_TOP + index * height
        box = (left, top, right, top + height)
        selected = index == view.selected
        if selected:
            mark_selection(canvas, box=box, focused=view.focused)
        DRAWERS[level](
            canvas, row=row, box=box, inverted=selected and view.focused,
        )


def draw_column(
    canvas: Canvas,
    *,
    view: ColumnView,
    edges: tuple[int, int],
    last: bool,
) -> None:
    """
    Draw one column's rows, note, scroll bar and the rule to its right.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : ColumnView
        The column.
    edges : tuple[int, int]
        Its left and right edges.
    last : bool
        Whether it is the last column, which has no rule.
    """
    left, right = edges
    inner = right if last else right - RULE_WIDTH
    if view.note is not None:
        draw_note(canvas, text=view.note, edges=(left, inner))
    if view.level is not None:
        draw_rows(
            canvas, view=view, level=view.level, left=left, right=inner,
        )
    if last:
        scroll_bar(canvas, view=view)
    else:
        canvas.rect(box=(inner, CHOOSER_TOP, right, HEIGHT), grey=INK)


def draw_header(canvas: Canvas, *, view: ChooserView) -> None:
    """
    Draw the black bar with the title on the left and the trail on the right.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : ChooserView
        The view.
    """
    canvas.rect(box=(0, 0, WIDTH, CHOOSER_TOP), grey=INK)
    right = WIDTH - 14 - canvas.text(
        at=(WIDTH - 14, HEADER_BASELINE),
        text=canvas.fit(text=view.trail, face=TRAIL_FACE, width=WIDTH / 2),
        face=TRAIL_FACE,
        grey=PAPER,
        anchor='rs',
    )
    canvas.text(
        at=(14, HEADER_BASELINE),
        text=canvas.fit(text=view.title, face=TITLE_FACE, width=right - 40),
        face=TITLE_FACE,
        grey=PAPER,
    )


def draw_chooser_view(*, view: ChooserView) -> NDArray[np.uint8]:
    """
    Draw a chooser view as a 1-bit frame.

    Parameters
    ----------
    view : ChooserView
        View-model.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    edges = column_edges(columns=view.columns)
    for index, column in enumerate(view.columns):
        draw_column(
            canvas,
            view=column,
            edges=edges[index],
            last=index == len(view.columns) - 1,
        )
    draw_header(canvas, view=view)
    if view.empty:
        canvas.text(
            at=(WIDTH / 2, EMPTY_Y),
            text=canvas.fit(
                text=view.empty, face=EMPTY_FACE, width=WIDTH - 64,
            ),
            face=EMPTY_FACE,
            grey=INK,
            anchor='mm',
        )
    return finish(canvas)
