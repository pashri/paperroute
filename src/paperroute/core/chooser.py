"""The route chooser: its columns, stages, knob handling and view-model."""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Final

from paperroute.core.strip_follow import origin_of
from paperroute.core.strip_model import ALL_TAG
from paperroute_transit.core.model import Departure, Pin, StopPlace
from paperroute_transit.core.modes import ModeSpec, spec_of
from paperroute_transit.core.reach import Reach, RowKind, StripKey


class Level(StrEnum):
    """What a chooser column lists."""

    KIND = 'kind'
    GROUP = 'group'
    ROUTE = 'route'
    LINE = 'line'
    TOWARDS = 'towards'
    REPLACEMENTS = 'replacements'
    STATION = 'station'
    TO = 'to'


ChooserPath = tuple[tuple[Level, str], ...]

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
    heading : bool
        Whether it is a block header: never focused or pressed.
    dim : bool
        Whether it is greyed out: never focused.
    place : StopPlace | None
        A station row's place, with its point; None for other rows.
    """

    key: str
    label: str
    badge: str = ''
    detail: str = ''
    tag: str = ''
    rule_above: bool = False
    level: Level | None = None
    heading: bool = False
    dim: bool = False
    place: StopPlace | None = None


def focusable(*, choice: Choice) -> bool:
    """
    Say whether a cursor may rest on a row.

    Parameters
    ----------
    choice : Choice
        The row.

    Returns
    -------
    bool
        True unless it is a heading or greyed out.
    """
    return not (choice.heading or choice.dim)


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


UNPICKED: Final[frozenset[RowKind]] = frozenset(
    {RowKind.APPROACH, RowKind.EXIT},
)  # strip rows the TO cursor never lands on


class Failure(StrEnum):
    """Why the last column or line asked for is missing."""

    LOAD = 'load'
    ABSENT = 'absent'  # the timetable has no such column


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

COLLAPSIBLE: Final[frozenset[Level]] = frozenset(
    {Level.GROUP, Level.ROUTE, Level.LINE},
)
NAMED_BY_BADGE: Final[frozenset[Level]] = frozenset({Level.ROUTE, Level.LINE})
PRESS_ONLY: Final[frozenset[Level]] = frozenset({Level.STATION, Level.TO})


@dataclass(frozen=True)
class DepartureLoad:
    """
    How the departures from the chooser's From stand.

    Attributes
    ----------
    loaded_until : datetime | None
        The end of the loaded window, or None before the first load.
    loading : bool
        True while a load is in flight.
    failed : bool
        True when the last load failed.
    """

    loaded_until: datetime | None = None
    loading: bool = False
    failed: bool = False


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
        From chosen, in the TO stage or after a station press.
    key : StripKey | None
        The TO stage's strip.
    to_cursor : int
        Rows from the From row to the TO stage's cursor; negative above.
    prefer : str | None
        Kind the KIND column's cursor starts on.
    failed : Failure | None
        Why the last column or line asked for is missing; None when it
        is not.
    departures : tuple[Departure, ...]
        The TO stage's departures from the From.
    load : DepartureLoad
        How those departures stand.
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
    prefer: str | None = None
    failed: Failure | None = None
    departures: tuple[Departure, ...] = ()
    load: DepartureLoad = field(default_factory=DepartureLoad)


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


def kind_specs(*, modes: Sequence[ModeSpec]) -> tuple[ModeSpec, ...]:
    """
    List the modes the KIND column offers.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        The city's modes.

    Returns
    -------
    tuple[ModeSpec, ...]
        The specs that sit under no other mode, in declared order.
    """
    return tuple(spec for spec in modes if spec.under is None)


def opened(
    *, shown: Pin, modes: Sequence[ModeSpec],
) -> tuple[ChooserState, ChooserRequest]:
    """
    Open the chooser.

    Parameters
    ----------
    shown : Pin
        The place on screen; its kind, or the mode it sits under, is
        the KIND cursor's start.
    modes : Sequence[ModeSpec]
        The city's modes.

    Returns
    -------
    tuple[ChooserState, ChooserRequest]
        The empty chooser and its request for the KIND column.
    """
    spec = spec_of(modes=modes, mode=shown.kind)
    prefer = spec.under if spec is not None and spec.under else shown.kind
    return ChooserState(waiting=(), prefer=prefer), ColumnRequest(path=())


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
        return replace(chooser, failed=Failure.LOAD)
    places: dict[str, StopPlace] = {}
    for place in line:
        places.setdefault(place.stop_id, place)
    return replace(chooser, line=tuple(places.values()), line_cursor=0)


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


def to_row(*, chooser: ChooserState, reach: Reach, origin: int) -> int:
    """
    Find the TO cursor's strip row.

    Parameters
    ----------
    chooser : ChooserState
        Chooser in the TO stage.
    reach : Reach
        Its strip.
    origin : int
        The strip's From row.

    Returns
    -------
    int
        The cursor's row; the From row when the cursor is off the strip
        or on an approach row.
    """
    target = origin + chooser.to_cursor
    on_strip = (
        0 <= target < len(reach.rows)
        and reach.rows[target].kind not in UNPICKED
    )
    return target if on_strip else origin


def turned_to(
    *, chooser: ChooserState, steps: int, reach: Reach | None,
) -> ChooserState:
    """
    Move the TO cursor over the strip's rows, skipping approach rows.

    It may stop on a closed fold, which a press opens.

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
        if row.kind not in UNPICKED
    ]
    at = rows.index(to_row(chooser=chooser, reach=reach, origin=origin))
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
        is on the From. No place while the strip is loading or with the
        cursor on a closed fold, which the press opens instead.
    """
    origin = None if reach is None else origin_of(reach=reach)
    if reach is None or origin is None:
        return chooser, None
    row = reach.rows[to_row(chooser=chooser, reach=reach, origin=origin)]
    if row.kind is RowKind.FOLD:
        return chooser, None
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
                line_cursor=0, failed=None,
            )
        case Stage.TO:
            return replace(
                chooser, stage=Stage.FROM, origin=None, key=None,
                to_cursor=0, departures=(), load=DepartureLoad(),
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
        The place: the path's kind, or after a replacements row the mode
        that row stands for, and the number of the last route or line
        chosen.

    Raises
    ------
    ValueError
        When no From has been chosen.
    """
    if chooser.origin is None:
        raise ValueError('The chooser has no From yet')
    path = path_to(chooser=chooser, index=chooser.focus)
    kind = next(
        (key for level, key in path if level is Level.REPLACEMENTS),
        path[0][1],
    )
    badges = [
        chosen(chooser=chooser, index=index).badge
        for index in range(len(path))
        if chooser.columns[index].level in NAMED_BY_BADGE
    ]
    return Pin(
        origin=chooser.origin,
        destination=destination,
        kind=kind,
        routes=tuple(badges[-1:]),
    )
