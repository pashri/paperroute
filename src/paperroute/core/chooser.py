"""The route chooser: its columns, stages, knob handling and view-model."""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from paperroute.core.model import Departure, Pin, RouteKind, StopPlace
from paperroute.core.reach import Reach, RowKind, StripKey
from paperroute.core.stop_view import DOT
from paperroute.core.strip_view import origin_of


class Level(StrEnum):
    """What a chooser column lists."""

    KIND = 'kind'
    GROUP = 'group'
    ROUTE = 'route'
    LINE = 'line'
    TOWARDS = 'towards'
    REPLACEMENTS = 'replacements'


ChooserPath = tuple[tuple[Level, str], ...]

KIND_ORDER: Final[tuple[RouteKind, ...]] = (
    RouteKind.BUS,
    RouteKind.TRAIN,
    RouteKind.METRO,
    RouteKind.LIGHT_RAIL,
    RouteKind.FERRY,
    RouteKind.SCHOOL,
    RouteKind.REGIONAL,
)
KIND_LABELS: Final[dict[RouteKind, str]] = {
    RouteKind.BUS: 'Bus',
    RouteKind.TRAIN: 'Train',
    RouteKind.METRO: 'Metro',
    RouteKind.LIGHT_RAIL: 'Light rail',
    RouteKind.FERRY: 'Ferry',
    RouteKind.SCHOOL: 'School',
    RouteKind.REGIONAL: 'Regional',
}
KIND_LETTERS: Final[dict[RouteKind, str]] = {
    RouteKind.BUS: 'B',
    RouteKind.TRAIN: 'T',
    RouteKind.METRO: 'M',
    RouteKind.LIGHT_RAIL: 'L',
    RouteKind.FERRY: 'F',
    RouteKind.SCHOOL: 'S',
    RouteKind.REGIONAL: 'R',
}


@dataclass(frozen=True)
class Choice:
    """
    One row of a chooser column.

    Attributes
    ----------
    key : str
        What the row stands for in a chooser path.
    label : str
        Main text.
    badge : str
        Route number, line code or kind letter; '' for none.
    detail : str
        Right-hand text, such as a count; '' for none.
    tag : str
        Short place tag telling repeated route numbers apart; '' for none.
    rule_above : bool
        Whether a rule separates this row from the one above.
    level : Level | None
        The path step's level when it differs from its column's, or None.
    """

    key: str
    label: str
    badge: str = ''
    detail: str = ''
    tag: str = ''
    rule_above: bool = False
    level: Level | None = None


@dataclass(frozen=True)
class Column:
    """
    One chooser column.

    Attributes
    ----------
    level : Level
        What it lists.
    items : tuple[Choice, ...]
        Its rows, in order.
    collapsed : bool
        Whether it was chosen automatically and is not drawn.
    """

    level: Level
    items: tuple[Choice, ...]
    collapsed: bool = False


class Stage(StrEnum):
    """What the chooser is picking."""

    LISTS = 'lists'
    FROM = 'from'
    TO = 'to'


@dataclass(frozen=True)
class ColumnRequest:
    """
    A request for the column that follows a path.

    Attributes
    ----------
    path : ChooserPath
        Steps chosen so far; () for the KIND column.
    """

    path: ChooserPath


@dataclass(frozen=True)
class LineRequest:
    """
    A request for the stations of a towards row.

    Attributes
    ----------
    towards : int
        Towards ID.
    """

    towards: int


@dataclass(frozen=True)
class StripRequest:
    """
    A request for the strip a chosen From and routes make.

    Attributes
    ----------
    key : StripKey
        Which runs feed the strip.
    """

    key: StripKey


ChooserRequest = ColumnRequest | LineRequest | StripRequest

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
COLLAPSIBLE: Final[frozenset[Level]] = frozenset(
    {Level.GROUP, Level.ROUTE, Level.LINE},
)
NAMED_BY_BADGE: Final[frozenset[Level]] = frozenset({Level.ROUTE, Level.LINE})
TITLES: Final[dict[Stage, str]] = {
    Stage.LISTS: 'Choose a route',
    Stage.FROM: 'Choose a stop',
    Stage.TO: 'Choose a destination',
}
LOADING_LISTS: Final[str] = 'Loading…'
NEEDS_UPDATE: Final[str] = 'Routes need a timetable update'


@dataclass(frozen=True)
class ChooserState:
    """
    Where the route chooser is.

    Attributes
    ----------
    columns : tuple[Column, ...]
        Loaded columns, left to right, collapsed ones included. The
        columns after the focus are its preview.
    cursors : tuple[int, ...]
        Each column's cursor.
    focus : int
        Index of the column the knob moves.
    waiting : ChooserPath | None
        Path of the column asked for, or None.
    stage : Stage
        Lists, picking the From or picking the Destination.
    towards : Choice | None
        Towards row pressed, from the FROM stage on.
    line : tuple[StopPlace, ...]
        Its stations, each once, in running order; () while loading.
    line_cursor : int
        Station picked in the FROM stage.
    origin : StopPlace | None
        From chosen, in the TO stage.
    key : StripKey | None
        The TO stage's strip.
    to_cursor : int
        Rows from the From row to the TO stage's cursor; negative above.
    prefer : RouteKind | None
        Kind the KIND column's cursor starts on.
    failed : bool
        True when the last column or line asked for could not be loaded.
    departures : tuple[Departure, ...]
        The TO stage's departures from the From.
    """

    columns: tuple[Column, ...] = ()
    cursors: tuple[int, ...] = ()
    focus: int = 0
    waiting: ChooserPath | None = None
    stage: Stage = Stage.LISTS
    towards: Choice | None = None
    line: tuple[StopPlace, ...] = ()
    line_cursor: int = 0
    origin: StopPlace | None = None
    key: StripKey | None = None
    to_cursor: int = 0
    prefer: RouteKind | None = None
    failed: bool = False
    departures: tuple[Departure, ...] = ()


@dataclass(frozen=True)
class ColumnView:
    """
    One chooser column as drawn.

    Attributes
    ----------
    level : Level
        What it lists.
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
    """

    level: Level
    items: tuple[Choice, ...]
    selected: int
    focused: bool
    first: int
    total: int


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


def clamped(*, value: int, count: int) -> int:
    """
    Keep an index within a list.

    Parameters
    ----------
    value : int
        Wanted index.
    count : int
        List length; at least 1.

    Returns
    -------
    int
        The nearest index from 0 to ``count - 1``.
    """
    return max(0, min(value, count - 1))


def chosen(*, chooser: ChooserState, index: int) -> Choice:
    """
    The choice under a column's cursor.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    index : int
        Column index.

    Returns
    -------
    Choice
        The choice.
    """
    return chooser.columns[index].items[chooser.cursors[index]]


def path_to(*, chooser: ChooserState, index: int) -> ChooserPath:
    """
    The path the cursors choose, up to and including a column.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    index : int
        Last column index.

    Returns
    -------
    ChooserPath
        One step per column: the choice's own level, else the column's,
        with the choice's key.
    """
    return tuple(
        (
            chosen(chooser=chooser, index=each).level
            or chooser.columns[each].level,
            chosen(chooser=chooser, index=each).key,
        )
        for each in range(index + 1)
    )


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
    Say whether the focus's cursor is on a towards row.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with at least one column.

    Returns
    -------
    bool
        True when pressing opens the FROM stage rather than a column.
    """
    return (
        chooser.columns[chooser.focus].level is Level.TOWARDS
        and chosen(chooser=chooser, index=chooser.focus).level is None
    )


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
        The chooser and its column request; no request for a towards row,
        whose next stage loads on the press.
    """
    end = chooser.focus + 1
    trimmed = replace(
        chooser,
        columns=chooser.columns[:end],
        cursors=chooser.cursors[:end],
        waiting=None,
        failed=False,
    )
    if opens_line(chooser=trimmed):
        return trimmed, None
    path = path_to(chooser=trimmed, index=chooser.focus)
    return replace(trimmed, waiting=path), ColumnRequest(path=path)


def opened(*, shown: Pin) -> tuple[ChooserState, ChooserRequest]:
    """
    Open the chooser.

    Parameters
    ----------
    shown : Pin
        The place on screen; its kind is the KIND cursor's start.

    Returns
    -------
    tuple[ChooserState, ChooserRequest]
        The empty chooser and its request for the KIND column.
    """
    return (
        ChooserState(waiting=(), prefer=shown.kind),
        ColumnRequest(path=()),
    )


def first_cursor(*, column: Column, prefer: RouteKind | None) -> int:
    """
    Choose where a new column's cursor starts.

    Parameters
    ----------
    column : Column
        New column.
    prefer : RouteKind | None
        Kind the KIND column starts on.

    Returns
    -------
    int
        The preferred kind's row in the KIND column, else 0.
    """
    if column.level is not Level.KIND:
        return 0
    return next(
        (
            index
            for index, choice in enumerate(column.items)
            if choice.key == prefer
        ),
        0,
    )


def with_column(
    *, chooser: ChooserState, path: ChooserPath, column: Column | None,
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

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser and its next request. A path other than the one
        waited for changes nothing. A single-row GROUP, ROUTE or LINE
        column is stored collapsed and its own next column asked for.
    """
    if path != chooser.waiting:
        return chooser, None
    if column is None:
        return replace(chooser, waiting=None, failed=True), None
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
        waiting=None,
    )
    if index == 0:
        return previewed(chooser=stored)
    if not collapsed:
        return stored, None
    following = path_to(chooser=stored, index=index)
    return replace(stored, waiting=following), ColumnRequest(path=following)


def with_line(
    *,
    chooser: ChooserState,
    towards: int,
    line: tuple[StopPlace, ...] | None,
) -> ChooserState:
    """
    Take in the stations of the pressed towards row.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    towards : int
        Towards ID the stations belong to.
    line : tuple[StopPlace, ...] | None
        Stations in running order, or None when they could not be loaded.

    Returns
    -------
    ChooserState
        The chooser with the line, each station once at its first call (a
        loop calls at some twice), and the cursor on its first station;
        unchanged unless it is picking a From for that towards row.
    """
    pressed_towards = chooser.towards
    if (
        chooser.stage is not Stage.FROM
        or pressed_towards is None
        or pressed_towards.key != str(towards)
    ):
        return chooser
    if line is None:
        return replace(chooser, failed=True)
    places: dict[str, StopPlace] = {}
    for place in line:
        places.setdefault(place.stop_id, place)
    return replace(chooser, line=tuple(places.values()), line_cursor=0)


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
    """
    if not chooser.columns:
        return chooser, None
    focus = chooser.focus
    old = chooser.cursors[focus]
    cursor = clamped(
        value=old + steps, count=len(chooser.columns[focus].items),
    )
    if cursor == old:
        return chooser, None
    cursors = (*chooser.cursors[:focus], cursor, *chooser.cursors[focus + 1:])
    return previewed(chooser=replace(chooser, cursors=cursors))


def turned_to(
    *, chooser: ChooserState, steps: int, reach: Reach | None,
) -> ChooserState:
    """
    Move the TO cursor over the strip's rows, skipping approach rows.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the TO stage.
    steps : int
        Detents; positive moves down.
    reach : Reach | None
        The chooser's strip, or None while loading.

    Returns
    -------
    ChooserState
        The chooser; unchanged while the strip is loading.
    """
    origin = None if reach is None else origin_of(reach=reach)
    if reach is None or origin is None:
        return chooser
    rows = [
        index
        for index, row in enumerate(reach.rows)
        if row.kind is not RowKind.APPROACH
    ]
    target = origin + chooser.to_cursor
    at = rows.index(target if target in rows else origin)
    row = rows[clamped(value=at + steps, count=len(rows))]
    return replace(chooser, to_cursor=row - origin)


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
        The chooser and its request; unchanged until the preview loads.
    """
    if not chooser.columns:
        return chooser, None
    if opens_line(chooser=chooser):
        towards = chosen(chooser=chooser, index=chooser.focus)
        return (
            replace(chooser, stage=Stage.FROM, towards=towards, waiting=None),
            LineRequest(towards=int(towards.key)),
        )
    preview = preview_of(chooser=chooser)
    if preview is None:
        return chooser, None
    return previewed(chooser=replace(chooser, focus=preview))


def pressed_from(
    *, chooser: ChooserState,
) -> tuple[ChooserState, ChooserRequest | None]:
    """
    Take the cursor's station as the From and ask for its strip.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the FROM stage.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | None]
        The chooser in the TO stage, cursor on the From, and its strip
        request; unchanged while the line is loading.
    """
    if not chooser.line:
        return chooser, None
    picked = replace(chooser, origin=chooser.line[chooser.line_cursor])
    pin = chosen_pin(chooser=picked, destination=None)
    key = StripKey(
        station=pin.origin.stop_id, kind=pin.kind, routes=pin.routes,
    )
    return (
        replace(picked, stage=Stage.TO, key=key, to_cursor=0),
        StripRequest(key=key),
    )


def pressed_to(
    *, chooser: ChooserState, reach: Reach | None,
) -> tuple[ChooserState, Pin | None]:
    """
    Commit the cursor's row as the Destination.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the TO stage.
    reach : Reach | None
        Its strip, or None while loading.

    Returns
    -------
    tuple[ChooserState, Pin | None]
        The chooser and the chosen place: no Destination when the cursor
        is on the From. No place while the strip is loading.
    """
    origin = None if reach is None else origin_of(reach=reach)
    if reach is None or origin is None:
        return chooser, None
    row = reach.rows[origin + chooser.to_cursor]
    destination = None if row.kind is RowKind.ORIGIN else row.place
    return chooser, chosen_pin(chooser=chooser, destination=destination)


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
        The chooser, the old focus now its preview; None from the first
        column, which closes the chooser.
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
        failed=False,
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
                line_cursor=0, failed=False,
            )
        case Stage.TO:
            return replace(
                chooser, stage=Stage.FROM, origin=None, key=None,
                to_cursor=0, departures=(),
            )


def chosen_pin(
    *, chooser: ChooserState, destination: StopPlace | None,
) -> Pin:
    """
    Make the place the chooser has picked.

    Parameters
    ----------
    chooser : ChooserState
        Chooser with a From.
    destination : StopPlace | None
        Destination, or None for every departure.

    Returns
    -------
    Pin
        The place: the path's kind, or Replacement after a replacement
        buses row, and the number of the last route or line chosen.

    Raises
    ------
    ValueError
        When no From has been chosen.
    """
    if chooser.origin is None:
        raise ValueError('The chooser has no From yet')
    path = path_to(chooser=chooser, index=chooser.focus)
    replaced = any(level is Level.REPLACEMENTS for level, _ in path)
    badges = [
        chosen(chooser=chooser, index=index).badge
        for index in range(len(path))
        if chooser.columns[index].level in NAMED_BY_BADGE
    ]
    return Pin(
        origin=chooser.origin,
        destination=destination,
        kind=RouteKind.REPLACEMENT if replaced else RouteKind(path[0][1]),
        routes=tuple(badges[-1:]),
    )


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
        the bottom edge, and from the top edge once scrolled.
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
        Columns left to right.
    """
    preview = preview_of(chooser=chooser)
    last = chooser.focus if preview is None else preview
    shown = [
        index
        for index, column in enumerate(chooser.columns[:last + 1])
        if not column.collapsed
    ][-SHOWN_COLUMNS:]
    return tuple(
        column_view(
            column=chooser.columns[index],
            cursor=chooser.cursors[index],
            focused=index == chooser.focus,
        )
        for index in shown
    )


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
        Title, trail of choices made, columns and any empty text: the
        timetable-update text after a failed load, the loading text before
        the first column.
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
        empty=(
            NEEDS_UPDATE
            if chooser.failed
            else LOADING_LISTS
            if chooser.waiting is not None and not chooser.columns
            else None
        ),
    )
