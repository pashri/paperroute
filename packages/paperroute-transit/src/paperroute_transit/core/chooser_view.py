"""The route chooser screen's view-model: titles, trail and columns."""

from dataclasses import dataclass, replace
from typing import Final

from paperroute_device.core.text import DOT
from paperroute_transit.core.chooser import (
    NAMED_BY_BADGE,
    Choice,
    ChooserState,
    Column,
    Failure,
    Level,
    Stage,
    chosen,
)
from paperroute_transit.core.chooser_lists import preview_of, reserves_preview
from paperroute_transit.core.chooser_station import focus_level
from paperroute_transit.core.strip_model import HINTS, PICK_TO

ROW_HEIGHTS: Final[dict[Level, int]] = {
    Level.KIND: 56,
    Level.GROUP: 31,
    Level.ROUTE: 40,
    Level.LINE: 31,
    Level.TOWARDS: 45,
    Level.STATION: 31,
    Level.TO: 31,
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
LEVEL_TITLES: Final[dict[Level, str]] = {
    Level.STATION: 'Choose a station',
    Level.TO: 'Choose a destination',
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
ABSENT_COLUMN: Final[ColumnView] = replace(FAILED_COLUMN, note=NEEDS_UPDATE)
RESERVED_COLUMN: Final[ColumnView] = replace(FAILED_COLUMN, note=None)


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
        Up to three columns; the focus's preview, or its reserved place,
        is last when the focus's level has one.
    empty : str | None
        Text shown in place of the columns, or None.
    hint : str | None
        What the knobs do, shown on a TO column; None elsewhere.
    """

    title: str
    trail: str
    columns: tuple[ColumnView, ...]
    empty: str | None
    hint: str | None = None


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


def preview_view(*, chooser: ChooserState) -> ColumnView:
    """
    Make the focus's preview column.

    Parameters
    ----------
    chooser : ChooserState
        Chooser whose focus has a preview.

    Returns
    -------
    ColumnView
        The drawn preview; else the timetable-update or can't-load column
        when the read failed; else the reserved, empty column.
    """
    preview = preview_of(chooser=chooser)
    if preview is not None:
        return column_view(
            column=chooser.columns[preview],
            cursor=chooser.cursors[preview],
            focused=False,
        )
    if chooser.failed is Failure.ABSENT:
        return ABSENT_COLUMN
    return FAILED_COLUMN if chooser.failed else RESERVED_COLUMN


def column_views(*, chooser: ChooserState) -> tuple[ColumnView, ...]:
    """
    Choose the columns drawn, fixed by the focus.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    tuple[ColumnView, ...]
        Columns left to right. A focus with a preview shows the last two
        drawn columns up to the focus, then the preview or its reserved
        place; any other focus shows the last three, then a note column
        when a read failed.
    """
    views = [
        column_view(
            column=column,
            cursor=chooser.cursors[index],
            focused=index == chooser.focus,
        )
        for index, column in enumerate(chooser.columns[:chooser.focus + 1])
        if not column.collapsed
    ]
    if reserves_preview(chooser=chooser):
        return (*views[-(SHOWN_COLUMNS - 1):], preview_view(chooser=chooser))
    if chooser.failed and chooser.columns:
        views.append(preview_view(chooser=chooser))
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
        Title, trail of choices made, columns, any empty text, and on a TO
        column its hint.
    """
    chosen_count = (
        chooser.focus if chooser.stage is Stage.LISTS else chooser.focus + 1
    )
    trail = DOT.join(
        step_label(chooser=chooser, index=index)
        for index in range(min(chosen_count, len(chooser.columns)))
    )
    level = focus_level(chooser=chooser)
    title = TITLES[chooser.stage]
    return ChooserView(
        title=title if level is None else LEVEL_TITLES.get(level, title),
        trail=trail,
        columns=column_views(chooser=chooser),
        empty=empty_text(chooser=chooser),
        hint=HINTS[PICK_TO] if level is Level.TO else None,
    )
