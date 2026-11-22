"""The LISTS stage: its columns, their previews and the knobs over them."""

from dataclasses import replace

from paperroute_transit.core.chooser import (
    COLLAPSIBLE,
    PRESS_ONLY,
    ChooserPath,
    ChooserRequest,
    ChooserState,
    Column,
    ColumnRequest,
    DepartureLoad,
    Failure,
    Level,
    LineRequest,
    Stage,
    chosen,
    clamped,
    focusable,
    path_to,
    pressed_from,
    pressed_to,
    turned_to,
)
from paperroute_transit.core.model import Pin
from paperroute_transit.core.reach import Reach
from paperroute_transit.core.sides import Kerbs
from paperroute_transit.core.strip_model import ALL_TAG


def preview_of(*, chooser: ChooserState) -> int | None:
    """
    Find the preview: the first drawn column after the focus.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    int | None
        Its index, or None while it is loading or there is none.
    """
    return next(
        (
            index
            for index in range(chooser.focus + 1, len(chooser.columns))
            if not chooser.columns[index].collapsed
        ),
        None,
    )


def opens_line(*, chooser: ChooserState) -> bool:
    """
    Say whether the focus's cursor asks for nothing until pressed.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with at least one column, the focus not empty.

    Returns
    -------
    bool
        True on a towards row, whose press opens the FROM stage, and in a
        STATION or TO column, whose press opens the TO column or commits.
    """
    level = chooser.columns[chooser.focus].level
    return level in PRESS_ONLY or (
        level is Level.TOWARDS
        and chosen(chooser=chooser, index=chooser.focus).level is None
    )


def is_empty(*, chooser: ChooserState, index: int) -> bool:
    """
    Say whether a column has no rows.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    index : int
        Column index.

    Returns
    -------
    bool
        True for an empty column, which is never focused.
    """
    return not chooser.columns[index].items


def previewed(
    *, chooser: ChooserState,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Drop the columns after the focus and ask for its cursor's preview.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with at least one column.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser, with no From, and its column request; no request for
        an empty column, or one whose next step loads on the press.
    """
    end = chooser.focus + 1
    trimmed = replace(
        chooser,
        columns=chooser.columns[:end],
        cursors=chooser.cursors[:end],
        waiting=None,
        failed=None,
        origin=None,
        departures=(),
        load=DepartureLoad(),
    )
    if is_empty(chooser=trimmed, index=chooser.focus) or opens_line(
        chooser=trimmed,
    ):
        return trimmed, None
    path = path_to(chooser=trimmed, index=chooser.focus)
    return replace(trimmed, waiting=path), ColumnRequest(path=path)


def first_cursor(*, column: Column, prefer: str | None) -> int:
    """
    Choose where a new column's cursor starts.

    Parameters
    ----------
    column : Column
        New column.
    prefer : str | None
        Kind the KIND column starts on.

    Returns
    -------
    int
        The preferred kind's row in the KIND column or the From's row in
        a TO column; failing those, the first row a cursor may rest on,
        else 0.
    """
    match column.level:
        case Level.KIND:
            first = [row.key == prefer for row in column.items]
        case Level.TO:
            first = [row.tag == ALL_TAG for row in column.items]
        case _:
            first = []
    resting = [focusable(choice=row) for row in column.items]
    return next(
        (index for index, hit in enumerate(first) if hit),
        next((index for index, hit in enumerate(resting) if hit), 0),
    )


def with_column(
    *,
    chooser: ChooserState,
    path: ChooserPath,
    column: Column | None,
    absent: bool = False,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Take in a loaded column.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    path : ChooserPath
        Path the column follows.
    column : Column | None
        The column, or None when it could not be loaded.
    absent : bool
        Whether a missing column is not in the timetable, rather than
        failed to load.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser and its next request. A path other than the one
        waited for changes nothing. A single-row GROUP, ROUTE or LINE
        column is stored collapsed and its own next column asked for. A
        TO column, asked for by a press, takes the focus.
    """
    if path != chooser.waiting:
        return chooser, None
    if column is None:
        failure = Failure.ABSENT if absent else Failure.LOAD
        return replace(chooser, waiting=None, failed=failure), None
    index = len(path)
    collapsed = column.level in COLLAPSIBLE and len(column.items) == 1
    stored = replace(
        chooser,
        columns=(
            *chooser.columns[:index], replace(column, collapsed=collapsed),
        ),
        cursors=(
            *chooser.cursors[:index],
            first_cursor(column=column, prefer=chooser.prefer),
        ),
        focus=index if column.level is Level.TO else chooser.focus,
        waiting=None,
    )
    if index == 0:
        return previewed(chooser=stored)
    if not collapsed:
        return stored, None
    following = path_to(chooser=stored, index=index)
    return replace(stored, waiting=following), ColumnRequest(path=following)


def stepped(*, column: Column, cursor: int, steps: int) -> int:
    """
    Move a cursor over the rows it may rest on.

    Parameters
    ----------
    column : Column
        Column.
    cursor : int
        Its cursor.
    steps : int
        Detents; positive moves down.

    Returns
    -------
    int
        The row ``steps`` resting rows on, stopping at either end; the
        same row when it is the only one.
    """
    rows = [
        index
        for index, choice in enumerate(column.items)
        if focusable(choice=choice)
    ]
    if cursor not in rows:
        return cursor
    at = rows.index(cursor)
    return rows[clamped(value=at + steps, count=len(rows))]


def turned_lists(
    *, chooser: ChooserState, steps: int,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Move the focus's cursor and ask for its preview.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the LISTS stage.
    steps : int
        Detents; positive moves down.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser and its request; unchanged when the cursor stays put.
        A TO column keeps its From and asks for nothing.
    """
    if not chooser.columns:
        return chooser, None
    focus = chooser.focus
    old = chooser.cursors[focus]
    column = chooser.columns[focus]
    cursor = stepped(column=column, cursor=old, steps=steps)
    if cursor == old:
        return chooser, None
    cursors = (*chooser.cursors[:focus], cursor, *chooser.cursors[focus + 1:])
    if column.level is Level.TO:
        return replace(chooser, cursors=cursors), None
    return previewed(chooser=replace(chooser, cursors=cursors))


def turned(
    *, chooser: ChooserState, steps: int, reach: Reach | None,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Handle a bottom-knob turn.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    steps : int
        Detents; positive moves down.
    reach : Reach | None
        The TO stage's strip, or None.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser and, in the LISTS stage, its preview request.
    """
    match chooser.stage:
        case Stage.LISTS:
            return turned_lists(chooser=chooser, steps=steps)
        case Stage.FROM:
            cursor = clamped(
                value=chooser.line_cursor + steps, count=len(chooser.line),
            )
            return replace(chooser, line_cursor=cursor), None
        case Stage.TO:
            moved = turned_to(chooser=chooser, steps=steps, reach=reach)
            return moved, None


def pressed_lists(
    *, chooser: ChooserState,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Open the focus's preview, or the FROM stage from a towards row.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the LISTS stage.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser and its request; unchanged until the preview loads,
        when the focus or the preview is empty, and in a STATION or TO
        column, whose presses `chooser_station` handles.
    """
    if not chooser.columns or is_empty(chooser=chooser, index=chooser.focus):
        return chooser, None
    if chooser.columns[chooser.focus].level in PRESS_ONLY:
        return chooser, None
    if opens_line(chooser=chooser):
        towards = chosen(chooser=chooser, index=chooser.focus)
        return (
            replace(chooser, stage=Stage.FROM, towards=towards, waiting=None),
            LineRequest(towards=int(towards.key)),
        )
    preview = preview_of(chooser=chooser)
    if preview is None or is_empty(chooser=chooser, index=preview):
        return chooser, None
    return previewed(chooser=replace(chooser, focus=preview))


def pressed(
    *, chooser: ChooserState, reach: Reach | None,
) -> tuple[ChooserState, ChooserRequest | Pin | None]:
    """
    Handle a bottom-knob press.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    reach : Reach | None
        The TO stage's strip, or None.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | Pin | None]
        The chooser and its request; a Pin means commit that place.
    """
    match chooser.stage:
        case Stage.LISTS:
            return pressed_lists(chooser=chooser)
        case Stage.FROM:
            return pressed_from(chooser=chooser)
        case Stage.TO:
            return pressed_to(chooser=chooser, reach=reach)


def backed_lists(*, chooser: ChooserState) -> ChooserState | None:
    """
    Move the focus back one drawn column.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the LISTS stage.

    Returns
    -------
    ChooserState | None
        The chooser, the old focus now its preview, with no From; None
        from the first column, which closes the chooser.
    """
    earlier = [
        index
        for index in range(chooser.focus)
        if not chooser.columns[index].collapsed
    ]
    if not earlier:
        return None
    end = chooser.focus + 1
    return replace(
        chooser,
        columns=chooser.columns[:end],
        cursors=chooser.cursors[:end],
        focus=earlier[-1],
        waiting=None,
        failed=None,
        origin=None,
        departures=(),
        load=DepartureLoad(),
    )


def backed(*, chooser: ChooserState) -> ChooserState | None:
    """
    Handle a top-knob press: go back one stage.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    ChooserState | None
        The chooser one stage back, or None when it closes.
    """
    match chooser.stage:
        case Stage.LISTS:
            return backed_lists(chooser=chooser)
        case Stage.FROM:
            return replace(
                chooser, stage=Stage.LISTS, towards=None, line=(),
                line_cursor=0, failed=None, kerbs=Kerbs(),
            )
        case Stage.TO:
            return replace(
                chooser, stage=Stage.FROM, origin=None, key=None,
                to_cursor=0, departures=(), load=DepartureLoad(),
            )
