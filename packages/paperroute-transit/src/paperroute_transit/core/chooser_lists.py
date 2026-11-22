"""The LISTS stage: its columns, their previews and the knobs over them."""

from dataclasses import replace

from paperroute_transit.core.chooser import (
    PRESS_ONLY,
    ChooserPath,
    ChooserRequest,
    ChooserState,
    Column,
    DepartureLoad,
    Failure,
    Level,
    LineRequest,
    PreviewsRequest,
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


def reserves_preview(*, chooser: ChooserState) -> bool:
    """
    Say whether the focus's level has a preview to show.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    bool
        True when the chooser has columns and its focus has rows and is
        not a column whose press opens the next stage.
    """
    return (
        bool(chooser.columns)
        and not is_empty(chooser=chooser, index=chooser.focus)
        and not opens_line(chooser=chooser)
    )


def previews_asked(
    *, chooser: ChooserState,
) -> tuple[ChooserState, PreviewsRequest]:
    """
    Mark the focus column's previews as being read and ask for them.

    Parameters
    ----------
    chooser : ChooserState
        Chooser whose focus has rows.

    Returns
    -------
    tuple[ChooserState, PreviewsRequest]
        The chooser with the focus's previews set to (), and the request
        for every row's preview.
    """
    focus = chooser.focus
    column = chooser.columns[focus]
    request = PreviewsRequest(
        path=path_to(chooser=chooser, index=focus - 1),
        steps=preview_steps(column=column),
    )
    marked = replace(column, previews=())
    return replace(
        chooser,
        columns=(
            *chooser.columns[:focus], marked, *chooser.columns[focus + 1:],
        ),
    ), request


def previewed(
    *, chooser: ChooserState,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Drop the columns after the focus and show its cursor's preview.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with at least one column.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser, with no From; the focus's previews request when it
        has none and its level has a preview; none while they are being
        read or once loaded, when the cursor's preview is installed.
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
    if not reserves_preview(chooser=trimmed):
        return trimmed, None
    previews = trimmed.columns[trimmed.focus].previews
    if previews is None:
        return previews_asked(chooser=trimmed)
    if not previews:
        return trimmed, None
    return opened_preview(chooser=trimmed), None


def previews_again(
    *, chooser: ChooserState,
) -> tuple[ChooserState, PreviewsRequest | None]:
    """
    Drop every column's previews and ask for the focus's again.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    tuple[ChooserState, PreviewsRequest | None]
        The chooser with the columns on screen kept and no previews; the
        focus's request in the LISTS stage with no column waited for and
        a level that has a preview, else none.
    """
    cleared = replace(
        chooser,
        columns=tuple(
            replace(column, previews=None) for column in chooser.columns
        ),
    )
    if (
        cleared.stage is not Stage.LISTS
        or cleared.waiting is not None
        or not reserves_preview(chooser=cleared)
    ):
        return cleared, None
    return previews_asked(chooser=cleared)


def preview_steps(*, column: Column) -> tuple[tuple[Level, str], ...]:
    """
    List each row's own step, in row order.

    Parameters
    ----------
    column : Column
        Column.

    Returns
    -------
    tuple[tuple[Level, str], ...]
        The row's own level, else the column's, with its key.
    """
    return tuple((row.level or column.level, row.key) for row in column.items)


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
        waited for changes nothing. The column is stored as given; the
        KIND column then has its previews asked for. A TO column, asked
        for by a press, takes the focus.
    """
    if path != chooser.waiting:
        return chooser, None
    if column is None:
        failure = Failure.ABSENT if absent else Failure.LOAD
        return replace(chooser, waiting=None, failed=failure), None
    index = len(path)
    stored = replace(
        chooser,
        columns=(*chooser.columns[:index], column),
        cursors=(
            *chooser.cursors[:index],
            first_cursor(column=column, prefer=chooser.prefer),
        ),
        focus=index if column.level is Level.TO else chooser.focus,
        waiting=None,
    )
    if index == 0:
        return previewed(chooser=stored)
    return stored, None


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


def opened_preview(*, chooser: ChooserState) -> ChooserState:
    """
    Show the focus cursor's preview from the focus's loaded previews.

    Parameters
    ----------
    chooser : ChooserState
        Chooser whose focus has its previews loaded.

    Returns
    -------
    ChooserState
        The columns after the focus replaced by the cursor's chain, each
        with its first cursor; failed is ABSENT when the chain is empty
        or ends collapsed, else None. Unchanged without previews.
    """
    focus = chooser.focus
    previews = chooser.columns[focus].previews
    cursor = chooser.cursors[focus]
    if previews is None or cursor >= len(previews):
        return chooser
    chain = previews[cursor]
    end = focus + 1
    absent = not chain or chain[-1].collapsed
    return replace(
        chooser,
        columns=(*chooser.columns[:end], *chain),
        cursors=(
            *chooser.cursors[:end],
            *(
                first_cursor(column=column, prefer=chooser.prefer)
                for column in chain
            ),
        ),
        failed=Failure.ABSENT if absent else None,
    )


def awaiting_previews(
    *, chooser: ChooserState, path: ChooserPath, count: int | None,
) -> bool:
    """
    Say whether a column on the path is reading previews for its rows.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    path : ChooserPath
        Steps leading to the column.
    count : int | None
        The number of rows the reply covers; None for a failure.

    Returns
    -------
    bool
        True when the column exists, follows the path and has its
        previews being read, over that many rows.
    """
    index = len(path)
    if index >= len(chooser.columns):
        return False
    column = chooser.columns[index]
    if column.previews != () or count not in (None, len(column.items)):
        return False
    return path_to(chooser=chooser, index=index - 1) == path


def with_previews(
    *,
    chooser: ChooserState,
    path: ChooserPath,
    previews: tuple[tuple[Column, ...], ...],
) -> ChooserState:
    """
    Keep a column's previews and show the focus cursor's.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    path : ChooserPath
        Steps leading to the column.
    previews : tuple[tuple[Column, ...], ...]
        One chain per row.

    Returns
    -------
    ChooserState
        The chooser with the previews on the column, and, when it is the
        LISTS stage's focus, the cursor's preview shown. Unchanged for a
        reply that no longer fits.
    """
    if not awaiting_previews(
        chooser=chooser, path=path, count=len(previews),
    ):
        return chooser
    index = len(path)
    column = replace(chooser.columns[index], previews=previews)
    kept = replace(
        chooser,
        columns=(
            *chooser.columns[:index], column, *chooser.columns[index + 1:],
        ),
    )
    if index == chooser.focus and chooser.stage is Stage.LISTS:
        return opened_preview(chooser=kept)
    return kept


def previews_failed(
    *, chooser: ChooserState, path: ChooserPath,
) -> ChooserState:
    """
    Take a failed previews read into the chooser.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    path : ChooserPath
        Steps leading to the column.

    Returns
    -------
    ChooserState
        The column's previews None and, when it is the LISTS stage's
        focus, the columns after it dropped and failed set to LOAD.
        Unchanged for a reply that no longer fits.
    """
    if not awaiting_previews(chooser=chooser, path=path, count=None):
        return chooser
    index = len(path)
    column = replace(chooser.columns[index], previews=None)
    end = index + 1
    failed = index == chooser.focus and chooser.stage is Stage.LISTS
    return replace(
        chooser,
        columns=(
            *chooser.columns[:index],
            column,
            *(() if failed else chooser.columns[end:]),
        ),
        cursors=chooser.cursors[:end] if failed else chooser.cursors,
        failed=Failure.LOAD if failed else chooser.failed,
    )
