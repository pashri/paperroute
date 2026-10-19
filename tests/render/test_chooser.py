"""Facts about the drawn chooser frames (no golden images)."""

from dataclasses import replace

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.core.chooser import Choice, Level
from paperroute.core.chooser_view import (
    CHOOSER_TOP,
    FAILED_COLUMN,
    ROW_HEIGHTS,
    ChooserView,
    ColumnView,
)
from paperroute.render.canvas import HEIGHT, INK, PAPER, WIDTH
from paperroute.render.chooser import COLUMN_WIDTHS, draw_chooser_view

LONG_NAME = 'Parramatta to Blacktown via a very long and winding road home'


def route_choice(*, label: str = 'Parramatta', tag: str = '') -> Choice:
    """
    Make a route row.

    Parameters
    ----------
    label : str
        Long name.
    tag : str
        Place tag.

    Returns
    -------
    Choice
        The row.
    """
    return Choice(key='606', label=label, badge='606', tag=tag)


def column(
    *,
    level: Level,
    items: tuple[Choice, ...],
    focused: bool = True,
    total: int | None = None,
) -> ColumnView:
    """
    Make a column whose first row is selected.

    Parameters
    ----------
    level : Level
        What it lists.
    items : tuple[Choice, ...]
        Rows.
    focused : bool
        Whether the knob moves it.
    total : int | None
        Rows in the whole column; the row count when None.

    Returns
    -------
    ColumnView
        The column.
    """
    return ColumnView(
        level=level,
        items=items,
        selected=0,
        focused=focused,
        first=0,
        total=len(items) if total is None else total,
    )


def view_of(*columns: ColumnView, empty: str | None = None) -> ChooserView:
    """
    Make a chooser view.

    Parameters
    ----------
    *columns : ColumnView
        Columns, left to right.
    empty : str | None
        Text in place of the columns.

    Returns
    -------
    ChooserView
        The view.
    """
    return ChooserView(
        title='Choose a route', trail='Bus', columns=columns, empty=empty,
    )


def groups(*, focused: bool) -> ColumnView:
    """
    Make a group column of three rows.

    Parameters
    ----------
    focused : bool
        Whether the knob moves it.

    Returns
    -------
    ColumnView
        The column.
    """
    items = tuple(
        Choice(key=label, label=label, detail='12')
        for label in ('1-99', '100s', '600s')
    )
    return column(level=Level.GROUP, items=items, focused=focused)


def dark(*, frame: NDArray[np.uint8], box: tuple[int, int, int, int]) -> float:
    """
    Measure how much of a box is ink.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        The frame.
    box : tuple[int, int, int, int]
        Left, top, right, bottom.

    Returns
    -------
    float
        Fraction of ink pixels.
    """
    left, top, right, bottom = box
    return float(np.mean(frame[top:bottom, left:right] == INK))


def test_frame_is_two_level() -> None:
    """Every pixel is ink or paper."""
    frame = draw_chooser_view(view=view_of(groups(focused=True)))
    assert frame.shape == (HEIGHT, WIDTH)
    assert set(np.unique(frame)) <= {INK, PAPER}


def test_focused_selection_is_inverted() -> None:
    """A focused selection is an ink fill under paper text."""
    frame = draw_chooser_view(view=view_of(groups(focused=True)))
    top = CHOOSER_TOP
    height = ROW_HEIGHTS[Level.GROUP]
    assert dark(frame=frame, box=(300, top + 4, 700, top + height - 4)) == 1
    assert dark(
        frame=frame, box=(0, top + 4, 100, top + height - 4),
    ) < 1
    below = top + height + 4
    assert dark(frame=frame, box=(300, below, 700, below + 10)) == 0


def test_unfocused_selection_is_outlined() -> None:
    """An unfocused selection is a 2 px outline round paper."""
    frame = draw_chooser_view(view=view_of(groups(focused=False)))
    top = CHOOSER_TOP
    height = ROW_HEIGHTS[Level.GROUP]
    assert dark(frame=frame, box=(300, top, 700, top + 2)) == 1
    bottom = top + height
    assert dark(frame=frame, box=(300, bottom - 2, 700, bottom)) == 1
    assert dark(frame=frame, box=(300, top + 4, 700, top + height - 4)) == 0


def test_last_column_fills_width() -> None:
    """The last column's selection runs to the right edge."""
    first = column(
        level=Level.KIND, items=(Choice(key='b', label='Bus', badge='B'),),
        focused=False,
    )
    frame = draw_chooser_view(view=view_of(first, groups(focused=True)))
    row = CHOOSER_TOP + ROW_HEIGHTS[Level.GROUP] // 2
    assert frame[row, WIDTH - 1] == INK
    assert frame[row, COLUMN_WIDTHS[Level.KIND] + 10] == INK


def test_failed_column_fills_width() -> None:
    """A level-less column is drawn to the right edge with its note."""
    frame = draw_chooser_view(
        view=view_of(groups(focused=False), FAILED_COLUMN),
    )
    left = COLUMN_WIDTHS[Level.GROUP]
    assert dark(frame=frame, box=(left + 10, 200, WIDTH, 300)) > 0
    assert dark(frame=frame, box=(WIDTH - 200, 200, WIDTH, 300)) == 0


def test_empty_column_note_is_drawn() -> None:
    """A column's note is drawn in place of its rows."""
    empty = ColumnView(
        level=Level.ROUTE, items=(), selected=0, focused=True, first=0,
        total=0, note='Nothing here',
    )
    frame = draw_chooser_view(view=view_of(empty))
    assert dark(frame=frame, box=(0, 100, WIDTH, 400)) > 0


def test_long_route_names_fit() -> None:
    """A long name stays left of the tag."""
    items = (route_choice(label=LONG_NAME, tag='SYD'),)
    frame = draw_chooser_view(
        view=view_of(column(level=Level.ROUTE, items=items, focused=False)),
    )
    plain = draw_chooser_view(
        view=view_of(column(
            level=Level.ROUTE,
            items=(route_choice(label=LONG_NAME),),
            focused=False,
        )),
    )
    top = CHOOSER_TOP + 8
    bottom = CHOOSER_TOP + ROW_HEIGHTS[Level.ROUTE] - 8
    differs = (
        frame[top:bottom, 500:WIDTH - 4] != plain[top:bottom, 500:WIDTH - 4]
    ).any(axis=0)
    tag_left = int(np.flatnonzero(differs)[0]) + 500
    name = frame[top:bottom, 80:tag_left - 4]
    assert (name == INK).any()
    assert tag_left < WIDTH - 20
    assert (frame[top:bottom, tag_left - 4:tag_left] == PAPER).all()


def test_scroll_bar_when_list_overflows() -> None:
    """A thumb is drawn at the right edge only when rows overflow."""
    items = tuple(route_choice() for _ in range(5))
    many = view_of(
        replace(
            column(level=Level.TOWARDS, items=items, focused=False, total=60),
            selected=4,
        ),
    )
    few = view_of(
        replace(
            column(level=Level.TOWARDS, items=items, focused=False),
            selected=4,
        ),
    )
    thumb = (WIDTH - 4, CHOOSER_TOP + 10, WIDTH, CHOOSER_TOP + 50)
    assert dark(frame=draw_chooser_view(view=many), box=thumb) == 1
    assert dark(frame=draw_chooser_view(view=few), box=thumb) == 0


def test_empty_text_centred() -> None:
    """The empty text sits in the middle under the header."""
    frame = draw_chooser_view(view=view_of(empty='Loading…'))
    ys, xs = np.nonzero(frame[CHOOSER_TOP:] == INK)
    assert ys.size
    assert abs((xs.min() + xs.max()) / 2 - WIDTH / 2) < 6
    assert xs.max() - xs.min() < WIDTH / 2


def test_header_shows_title_and_trail() -> None:
    """The header is a black bar with paper text at each end."""
    frame = draw_chooser_view(view=view_of(groups(focused=True)))
    assert dark(frame=frame, box=(0, 0, WIDTH, CHOOSER_TOP)) > 0.7
    assert (frame[:CHOOSER_TOP, :200] == PAPER).any()
    assert (frame[:CHOOSER_TOP, WIDTH - 200:] == PAPER).any()


ROW_ITEMS: tuple[Choice, ...] = (
    Choice(key='a', label='Alpha', badge='T1', detail='4', tag='X'),
    Choice(key='b', label='Beta', badge='T2', rule_above=True),
)


@pytest.mark.parametrize(
    ('level', 'x', 'row'),
    [
        (Level.KIND, 12, 0),
        (Level.ROUTE, 12, 0),
        (Level.LINE, 12, 0),
    ],
)
def test_badge_box_is_ink_at_row_start(level: Level, x: int, row: int) -> None:
    """Rows with a badge or letter start with a solid ink box."""
    view = view_of(column(level=level, items=ROW_ITEMS, focused=False))
    frame = draw_chooser_view(view=view)
    top = CHOOSER_TOP + ROW_HEIGHTS[level] * (row + 1)
    middle = top + ROW_HEIGHTS[level] // 2
    assert frame[middle, x] == INK
    assert frame[middle, x - 8] == PAPER


def test_group_row_count_is_right_aligned() -> None:
    """A group row's count sits at the column's right, its label left."""
    view = view_of(groups(focused=False), FAILED_COLUMN)
    frame = draw_chooser_view(view=view)
    row = (CHOOSER_TOP + ROW_HEIGHTS[Level.GROUP] + 2,
           CHOOSER_TOP + 2 * ROW_HEIGHTS[Level.GROUP] - 2)
    right = COLUMN_WIDTHS[Level.GROUP]
    assert dark(frame=frame, box=(right - 30, row[0], right - 8, row[1])) > 0
    assert dark(frame=frame, box=(10, row[0], 60, row[1])) > 0


def test_route_tag_is_outlined_at_right() -> None:
    """A route's tag is a box at the right with paper inside."""
    view = view_of(column(
        level=Level.ROUTE, items=ROW_ITEMS[:1], focused=False,
    ))
    frame = draw_chooser_view(view=view)
    middle = CHOOSER_TOP + ROW_HEIGHTS[Level.ROUTE] // 2
    right = WIDTH - 10
    assert frame[middle, right - 1] == INK
    assert frame[middle, right - 3] == PAPER


def test_line_rule_above_is_dotted() -> None:
    """A rule above a line row is dotted: ink and paper alternate."""
    view = view_of(column(
        level=Level.LINE, items=ROW_ITEMS, focused=False,
    ))
    frame = draw_chooser_view(view=view)
    rule = frame[CHOOSER_TOP + ROW_HEIGHTS[Level.LINE], 20:200]
    assert (rule == INK).any()
    assert (rule == PAPER).any()


def test_towards_count_is_right_aligned() -> None:
    """A towards row's count sits at the column's right edge."""
    view = view_of(column(
        level=Level.TOWARDS, items=ROW_ITEMS[:1], focused=False,
    ))
    frame = draw_chooser_view(view=view)
    top = CHOOSER_TOP
    box = (WIDTH - 40, top + 10, WIDTH - 10, top + 40)
    assert dark(frame=frame, box=box) > 0


def test_scroll_thumb_shows_over_a_focused_selection() -> None:
    """The thumb is paper where it crosses an ink-filled selection."""
    items = tuple(route_choice() for _ in range(5))
    view = view_of(column(
        level=Level.TOWARDS, items=items, focused=True, total=60,
    ))
    frame = draw_chooser_view(view=view)
    row = (CHOOSER_TOP + 5, CHOOSER_TOP + 40)
    assert dark(frame=frame, box=(WIDTH - 4, row[0], WIDTH, row[1])) == 0
    assert dark(frame=frame, box=(WIDTH - 30, row[0], WIDTH - 6, row[1])) == 1
