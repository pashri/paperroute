"""The route chooser screen's view-model: titles, trail and columns."""

from dataclasses import dataclass
from typing import Final

from paperroute.core.chooser import (
    NAMED_BY_BADGE,
    Choice,
    ChooserState,
    Column,
    Level,
    Stage,
    chosen,
    preview_of,
)
from paperroute.core.stop_view import DOT

ROW_HEIGHTS: Final[dict[Level, int]] = {
    Level.KIND: 56,
    Level.GROUP: 31,
    Level.ROUTE: 40,
    Level.LINE: 31,
    Level.TOWARDS: 45,
}
CHOOSER_TOP: Final[int] = 40
CHOOSER_BOTTOM: Final[int] = 480
COLUMN_MARGIN: Final[int] = 2
SHOWN_COLUMNS: Final[int] = 3
TITLES: Final[dict[Stage, str]] = {
    Stage.LISTS: 'Choose a route',
    Stage.FROM: 'Choose a stop',
    Stage.TO: 'Choose a destination',
}
LOADING_LISTS: Final[str] = 'Loading…'
NEEDS_UPDATE: Final[str] = 'Routes need a timetable update'
NOTHING_HERE: Final[str] = 'Nothing here'
CANT_LOAD: Final[str] = "Can't load"


@dataclass(frozen=True)
class ColumnView:
    """
    One chooser column as drawn.

    Attributes
    ----------
    level : Level | None
        What it lists; None for a column that failed to load.
    items : tuple[Choice, ...]
        The rows that fit, from ``first``.
    selected : int
        Cursor's index within ``items``.
    focused : bool
        Whether the knob moves this column.
    first : int
        Index of the first visible row.
    total : int
        Rows in the whole column.
    note : str | None
        Text shown in place of the rows of an empty or failed column.
    """

    level: Level | None
    items: tuple[Choice, ...]
    selected: int
    focused: bool
    first: int
    total: int
    note: str | None = None


FAILED_COLUMN: Final[ColumnView] = ColumnView(
    level=None, items=(), selected=0, focused=False, first=0, total=0,
    note=CANT_LOAD,
)


@dataclass(frozen=True)
class ChooserView:
    """
    What the chooser screen shows.

    Attributes
    ----------
    title : str
        What is being chosen.
    trail : str
        The choices made so far.
    columns : tuple[ColumnView, ...]
        Up to three columns, ending with the focus's preview.
    empty : str | None
        Text shown in place of the columns, or None.
    """

    title: str
    trail: str
    columns: tuple[ColumnView, ...]
    empty: str | None


def step_label(*, chooser: ChooserState, index: int) -> str:
    """
    Name a chosen step for the trail.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    index : int
        Column index.

    Returns
    -------
    str
        A route's or line's number, else the choice's label.
    """
    choice = chosen(chooser=chooser, index=index)
    if chooser.columns[index].level in NAMED_BY_BADGE and choice.badge:
        return choice.badge
    return choice.label


def route_title(*, chooser: ChooserState) -> str:
    """
    Name the route and towards row being picked on.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM or TO stage.

    Returns
    -------
    str
        For example '606 to Parramatta': the number of the last route or
        line chosen, then the towards row pressed.
    """
    number = next(
        (
            step_label(chooser=chooser, index=index)
            for index in reversed(range(len(chooser.columns)))
            if index < chooser.focus
            and chooser.columns[index].level in NAMED_BY_BADGE
        ),
        '',
    )
    towards = chooser.towards
    if towards is None:
        return number
    return f'{number} to {towards.label}'.strip()


def column_view(
    *, column: Column, cursor: int, focused: bool,
) -> ColumnView:
    """
    Window a column so its cursor stays clear of the edges.

    Parameters
    ----------
    column : Column
        Column.
    cursor : int
        Its cursor.
    focused : bool
        Whether the knob moves it.

    Returns
    -------
    ColumnView
        The rows that fit, the cursor at least ``COLUMN_MARGIN`` rows from
        the bottom edge, and from the top edge once scrolled; a note for
        an empty column.
    """
    fits = (CHOOSER_BOTTOM - CHOOSER_TOP) // ROW_HEIGHTS[column.level]
    total = len(column.items)
    first = max(0, min(cursor + COLUMN_MARGIN + 1 - fits, total - fits))
    return ColumnView(
        level=column.level,
        items=column.items[first:first + fits],
        selected=cursor - first,
        focused=focused,
        first=first,
        total=total,
        note=None if total else NOTHING_HERE,
    )


def column_views(*, chooser: ChooserState) -> tuple[ColumnView, ...]:
    """
    The last three drawn columns up to the focus's preview.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    tuple[ColumnView, ...]
        Columns left to right. A preview that failed to load shows as a
        column with a note.
    """
    preview = preview_of(chooser=chooser)
    last = chooser.focus if preview is None else preview
    views = [
        column_view(
            column=column,
            cursor=chooser.cursors[index],
            focused=index == chooser.focus,
        )
        for index, column in enumerate(chooser.columns[:last + 1])
        if not column.collapsed
    ]
    if chooser.failed and chooser.columns:
        views.append(FAILED_COLUMN)
    return tuple(views[-SHOWN_COLUMNS:])


def empty_text(*, chooser: ChooserState) -> str | None:
    """
    Choose the text shown in place of all the columns.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    str | None
        The timetable-update text when the first column failed to load,
        the loading text while it loads, else None.
    """
    if chooser.columns:
        return None
    if chooser.failed:
        return NEEDS_UPDATE
    return LOADING_LISTS if chooser.waiting is not None else None


def build_chooser_view(*, chooser: ChooserState) -> ChooserView:
    """
    Build what the chooser screen shows.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    ChooserView
        Title, trail of choices made, columns and any empty text.
    """
    chosen_count = (
        chooser.focus if chooser.stage is Stage.LISTS else chooser.focus + 1
    )
    trail = DOT.join(
        step_label(chooser=chooser, index=index)
        for index in range(min(chosen_count, len(chooser.columns)))
    )
    return ChooserView(
        title=TITLES[chooser.stage],
        trail=trail,
        columns=column_views(chooser=chooser),
        empty=empty_text(chooser=chooser),
    )
