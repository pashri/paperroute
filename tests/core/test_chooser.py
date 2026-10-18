"""Tests for the route chooser's state and view-model."""

from typing import Final

import pytest

from paperroute.core.chooser import (
    Choice,
    ChooserPath,
    ChooserRequest,
    ChooserState,
    Column,
    ColumnRequest,
    Level,
    LineRequest,
    Stage,
    StripRequest,
    backed,
    build_chooser_view,
    chosen_pin,
    opened,
    path_to,
    pressed,
    turned,
    with_column,
    with_line,
)
from paperroute.core.model import Pin, RouteKind, StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind, StripKey
from tests.core.factories import place

KINDS: Final[Column] = Column(
    level=Level.KIND,
    items=(
        Choice(key='bus', label='Bus', badge='B'),
        Choice(key='train', label='Train', badge='T'),
        Choice(key='metro', label='Metro', badge='M'),
    ),
)
GROUPS: Final[Column] = Column(
    level=Level.GROUP,
    items=(
        Choice(key='1-99', label='1-99'),
        Choice(key='600s', label='600s'),
    ),
)
ROUTES: Final[Column] = Column(
    level=Level.ROUTE,
    items=(
        Choice(key='r606', label='Parramatta to Hawkesbury', badge='606'),
        Choice(key='r609', label='Parramatta to Castle Hill', badge='609'),
    ),
)
TOWARDS: Final[Column] = Column(
    level=Level.TOWARDS,
    items=(
        Choice(key='11', label='Parramatta', detail='40'),
        Choice(key='12', label='Hawkesbury', detail='38'),
    ),
)
METRO_LINES: Final[Column] = Column(
    level=Level.LINE,
    items=(Choice(key='m1', label='North West & Bankstown', badge='M1'),),
)
TRAIN_LINES: Final[Column] = Column(
    level=Level.LINE,
    items=(
        Choice(key='t1', label='North Shore & Western', badge='T1'),
        Choice(key='t2', label='Leppington & Inner West', badge='T2'),
    ),
)
T1_TOWARDS: Final[Column] = Column(
    level=Level.TOWARDS,
    items=(
        Choice(key='21', label='Emu Plains', detail='90'),
        Choice(
            key='replacements', label='Replacement buses',
            detail='2 routes', level=Level.REPLACEMENTS,
        ),
    ),
)
T1_BUSES: Final[Column] = Column(
    level=Level.ROUTE,
    items=(
        Choice(key='b1', label='Strathfield to Parramatta', badge='T1X'),
        Choice(key='b2', label='Penrith to Emu Plains', badge='T1Y'),
    ),
)
LINE: Final[tuple[StopPlace, ...]] = (
    place('S1', name='Parramatta'),
    place('S2', name='Northmead'),
    place('S3', name='Baulkham Hills'),
)
BUS_606: Final[ChooserPath] = (
    (Level.KIND, 'bus'),
    (Level.GROUP, '600s'),
    (Level.ROUTE, 'r606'),
)


def row(stop_id: str, kind: RowKind) -> ReachRow:
    """
    A strip row with no indent.

    Parameters
    ----------
    stop_id : str
        Station ID.
    kind : RowKind
        What the row shows.

    Returns
    -------
    ReachRow
        The row.
    """
    return ReachRow(
        place=place(stop_id), kind=kind, depth=0, above=False, parent=None,
    )


REACH: Final[Reach] = Reach(
    rows=(
        row('A1', RowKind.APPROACH),
        row('S2', RowKind.ORIGIN),
        row('S3', RowKind.MAIN),
        row('S4', RowKind.MAIN),
    ),
    stations=2,
    station_of={},
)


def fed(
    chooser: ChooserState,
    request: ChooserRequest | Pin | None,
    column: Column | None,
) -> tuple[ChooserState, ChooserRequest | Pin | None]:
    """
    Answer a column request.

    Parameters
    ----------
    chooser : ChooserState
        Chooser waiting for a column.
    request : ChooserRequest | Pin | None
        Its request; must be a ColumnRequest.
    column : Column | None
        The answer.

    Returns
    -------
    tuple[ChooserState, ChooserRequest | Pin | None]
        The chooser and its next request.
    """
    assert isinstance(request, ColumnRequest)
    return with_column(chooser=chooser, path=request.path, column=column)


def opened_at_kind(kind: RouteKind | None = None) -> ChooserState:
    """
    Open the chooser and load the KIND column.

    Parameters
    ----------
    kind : RouteKind | None
        Shown place's kind.

    Returns
    -------
    ChooserState
        The chooser, waiting for the KIND cursor's preview.
    """
    chooser, request = opened(shown=Pin(origin=place('X'), kind=kind))
    chooser, _ = fed(chooser, request, KINDS)
    return chooser


def at_towards() -> ChooserState:
    """
    Walk Bus, 600s, 606 to the focused TOWARDS column.

    Returns
    -------
    ChooserState
        The chooser focused on 606's TOWARDS column.
    """
    chooser = opened_at_kind()
    chooser, request = fed(chooser, ColumnRequest(path=BUS_606[:1]), GROUPS)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, None)  # 1-99 fails
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    chooser, request = fed(chooser, request, ROUTES)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, TOWARDS)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    return chooser


def at_from() -> ChooserState:
    """
    Press 606's first towards row and load its line.

    Returns
    -------
    ChooserState
        The chooser in the FROM stage, cursor on the first station.
    """
    chooser, _ = pressed(chooser=at_towards(), reach=None)
    return with_line(chooser=chooser, towards=11, line=LINE)


def at_to() -> ChooserState:
    """
    Pick Northmead as the From.

    Returns
    -------
    ChooserState
        The chooser in the TO stage, cursor on the From.
    """
    chooser, _ = turned(chooser=at_from(), steps=1, reach=None)
    chooser, _ = pressed(chooser=chooser, reach=None)
    return chooser


def test_opened_asks_for_kind_column() -> None:
    chooser, request = opened(
        shown=Pin(origin=place('X'), kind=RouteKind.TRAIN),
    )
    assert request == ColumnRequest(path=())
    assert chooser.waiting == ()
    assert chooser.prefer is RouteKind.TRAIN
    assert chooser.stage is Stage.LISTS
    assert not chooser.columns


def test_kind_cursor_starts_on_current_kind() -> None:
    chooser, asked = opened(
        shown=Pin(origin=place('X'), kind=RouteKind.TRAIN),
    )
    chooser, request = fed(chooser, asked, KINDS)
    assert chooser.cursors == (1,)
    assert request == ColumnRequest(path=((Level.KIND, 'train'),))
    assert chooser.waiting == ((Level.KIND, 'train'),)


def test_turn_requests_preview_of_new_cursor() -> None:
    chooser = opened_at_kind()
    chooser, _ = fed(chooser, ColumnRequest(path=BUS_606[:1]), GROUPS)
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    assert request == ColumnRequest(path=((Level.KIND, 'train'),))
    assert chooser.cursors == (1,)
    assert len(chooser.columns) == 1
    chooser, request = turned(chooser=chooser, steps=5, reach=None)
    assert chooser.cursors == (2,)
    assert request == ColumnRequest(path=((Level.KIND, 'metro'),))
    unchanged, request = turned(chooser=chooser, steps=1, reach=None)
    assert (unchanged, request) == (chooser, None)


def test_stale_column_is_ignored() -> None:
    chooser = opened_at_kind()
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    after, request = with_column(
        chooser=chooser, path=((Level.KIND, 'bus'),), column=GROUPS,
    )
    assert (after, request) == (chooser, None)


def test_press_before_preview_loaded_does_nothing() -> None:
    chooser = opened_at_kind()
    after, request = pressed(chooser=chooser, reach=None)
    assert (after, request) == (chooser, None)
    empty, _ = opened(shown=Pin(origin=place('X')))
    assert pressed(chooser=empty, reach=None) == (empty, None)
    assert turned(chooser=empty, steps=1, reach=None) == (empty, None)
    loading, _ = pressed(chooser=at_towards(), reach=None)
    assert pressed(chooser=loading, reach=None) == (loading, None)
    with pytest.raises(ValueError, match='no From'):
        chosen_pin(chooser=loading, destination=None)


def test_single_line_column_collapses() -> None:
    chooser = opened_at_kind(kind=RouteKind.METRO)
    chooser, request = fed(chooser, ColumnRequest(
        path=((Level.KIND, 'metro'),),
    ), METRO_LINES)
    assert chooser.columns[1].collapsed
    assert request == ColumnRequest(
        path=((Level.KIND, 'metro'), (Level.LINE, 'm1')),
    )
    chooser, request = fed(chooser, request, TOWARDS)
    assert request is None
    view = build_chooser_view(chooser=chooser)
    assert [column.level for column in view.columns] == [
        Level.KIND, Level.TOWARDS,
    ]
    chooser, request = pressed(chooser=chooser, reach=None)
    assert chooser.focus == 2
    assert request is None


def test_towards_press_asks_for_line() -> None:
    chooser, request = pressed(chooser=at_towards(), reach=None)
    assert request == LineRequest(towards=11)
    assert chooser.stage is Stage.FROM
    assert chooser.towards == TOWARDS.items[0]
    assert with_line(chooser=chooser, towards=12, line=LINE) == chooser
    loop = (*LINE, LINE[0])
    chooser = with_line(chooser=chooser, towards=11, line=loop)
    assert chooser.line == LINE
    assert with_line(chooser=chooser, towards=11, line=None).failed


def test_from_press_asks_for_strip_with_chosen_routes() -> None:
    chooser, _ = turned(chooser=at_from(), steps=9, reach=None)
    assert chooser.line_cursor == 2
    chooser, _ = turned(chooser=chooser, steps=-1, reach=None)
    chooser, request = pressed(chooser=chooser, reach=None)
    key = StripKey(station='S2', kind=RouteKind.BUS, routes=('606',))
    assert request == StripRequest(key=key)
    assert chooser.stage is Stage.TO
    assert chooser.origin == LINE[1]
    assert chooser.key == key
    assert chooser.to_cursor == 0


def test_to_press_on_from_commits_no_destination() -> None:
    chooser = at_to()
    assert pressed(chooser=chooser, reach=None) == (chooser, None)
    _, pin = pressed(chooser=chooser, reach=REACH)
    assert pin == Pin(
        origin=LINE[1], destination=None, kind=RouteKind.BUS,
        routes=('606',),
    )


def test_to_press_commits_destination() -> None:
    chooser, request = turned(chooser=at_to(), steps=2, reach=REACH)
    assert request is None
    _, pin = pressed(chooser=chooser, reach=REACH)
    assert isinstance(pin, Pin)
    assert pin.destination == place('S4')


def test_to_cursor_skips_approach_rows() -> None:
    chooser, _ = turned(chooser=at_to(), steps=-1, reach=REACH)
    assert chooser.to_cursor == 0
    chooser, _ = turned(chooser=chooser, steps=9, reach=REACH)
    assert chooser.to_cursor == 2
    chooser, _ = turned(chooser=chooser, steps=-9, reach=REACH)
    assert chooser.to_cursor == 0
    assert turned(chooser=chooser, steps=1, reach=None) == (chooser, None)


def test_back_walks_stages_then_closes() -> None:
    chooser = backed(chooser=at_to())
    assert chooser is not None
    assert chooser.stage is Stage.FROM
    assert (chooser.origin, chooser.key) == (None, None)
    assert chooser.line == LINE
    chooser = backed(chooser=chooser)
    assert chooser is not None
    assert chooser.stage is Stage.LISTS
    assert chooser.line == ()
    assert chooser.focus == 3
    focuses = []
    while chooser is not None:
        focuses.append(chooser.focus)
        assert len(chooser.columns) == chooser.focus + 2 or chooser.focus == 3
        chooser = backed(chooser=chooser)
    assert focuses == [3, 2, 1, 0]


def test_replacement_route_commits_replacement_kind() -> None:
    chooser = opened_at_kind(kind=RouteKind.TRAIN)
    train = ((Level.KIND, 'train'),)
    chooser, _ = fed(chooser, ColumnRequest(path=train), TRAIN_LINES)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, _ = fed(chooser, request, T1_TOWARDS)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request is None
    chooser, request = turned(chooser=chooser, steps=1, reach=None)
    assert request == ColumnRequest(path=(
        *train, (Level.LINE, 't1'), (Level.REPLACEMENTS, 'replacements'),
    ))
    chooser, request = fed(chooser, request, T1_BUSES)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = fed(chooser, request, TOWARDS)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request == LineRequest(towards=11)
    chooser = with_line(chooser=chooser, towards=11, line=LINE)
    chooser, request = pressed(chooser=chooser, reach=None)
    assert request == StripRequest(key=StripKey(
        station='S1', kind=RouteKind.REPLACEMENT, routes=('T1X',),
    ))


def test_view_shows_last_three_columns_with_preview() -> None:
    chooser = opened_at_kind()
    chooser, _ = fed(chooser, ColumnRequest(path=BUS_606[:1]), GROUPS)
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    many = Column(level=Level.ROUTE, items=tuple(
        Choice(key=f'r{number}', label='Somewhere', badge=str(number))
        for number in range(600, 630)
    ))
    chooser, _ = fed(chooser, ColumnRequest(path=BUS_606[:2]), many)
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, request = turned(chooser=chooser, steps=20, reach=None)
    chooser, _ = fed(chooser, request, TOWARDS)
    view = build_chooser_view(chooser=chooser)
    assert [column.level for column in view.columns] == [
        Level.GROUP, Level.ROUTE, Level.TOWARDS,
    ]
    assert [column.focused for column in view.columns] == [
        False, True, False,
    ]
    routes = view.columns[1]
    assert (routes.first, routes.total, len(routes.items)) == (12, 30, 11)
    assert routes.items[routes.selected].badge == '620'
    assert routes.selected == 8
    assert view.columns[0].items[view.columns[0].selected].label == '600s'


def test_trail_and_titles() -> None:
    lists = at_towards()
    view = build_chooser_view(chooser=lists)
    assert (view.title, view.trail) == ('Choose a route', 'Bus · 600s · 606')
    assert view.empty is None
    choosing_from, _ = pressed(chooser=lists, reach=None)
    view = build_chooser_view(chooser=choosing_from)
    assert view.title == 'Choose a stop'
    assert view.trail == 'Bus · 600s · 606 · Parramatta'
    assert build_chooser_view(chooser=at_to()).title == 'Choose a destination'


def test_empty_text_while_loading_and_failed() -> None:
    chooser, request = opened(shown=Pin(origin=place('X')))
    assert build_chooser_view(chooser=chooser).empty == 'Loading…'
    chooser, _ = fed(chooser, request, None)
    view = build_chooser_view(chooser=chooser)
    assert view.empty == 'Routes need a timetable update'
    assert chooser.waiting is None


def test_path_to_uses_choice_level() -> None:
    chooser = opened_at_kind(kind=RouteKind.TRAIN)
    train = ((Level.KIND, 'train'),)
    chooser, _ = fed(chooser, ColumnRequest(path=train), TRAIN_LINES)
    chooser, request = pressed(chooser=chooser, reach=None)
    chooser, _ = fed(chooser, request, T1_TOWARDS)
    chooser, _ = pressed(chooser=chooser, reach=None)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    assert path_to(chooser=chooser, index=2)[-1] == (
        Level.REPLACEMENTS, 'replacements',
    )
