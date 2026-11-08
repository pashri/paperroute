"""Tests for the route chooser's state."""

from dataclasses import replace

import pytest

from paperroute.city import SydneyMode
from paperroute.core.chooser import (
    Choice,
    Column,
    ColumnRequest,
    Level,
    LineRequest,
    Stage,
    StripRequest,
    backed,
    chosen_pin,
    opened,
    path_to,
    pressed,
    turned,
    with_column,
    with_line,
)
from paperroute.core.chooser_station import knob_pressed
from paperroute.core.chooser_view import build_chooser_view
from paperroute_testkit.core.factories import place
from paperroute_transit.core.model import Pin
from paperroute_transit.core.reach import RowKind, StripKey
from tests.core.chooser_walks import (
    BUS_606,
    GROUPS,
    KINDS,
    LINE,
    METRO_LINES,
    REACH,
    T1,
    T1_PLACES,
    T1_STATIONS,
    T1_TO,
    TOWARDS,
    at_from,
    at_replacement_towards,
    at_stations,
    at_to,
    at_towards,
    fed,
    opened_at_kind,
    row,
)


def test_opened_asks_for_kind_column() -> None:
    chooser, request = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.TRAIN),
    )
    assert request == ColumnRequest(path=())
    assert chooser.waiting == ()
    assert chooser.prefer == SydneyMode.TRAIN
    assert chooser.stage is Stage.LISTS
    assert not chooser.columns


def test_kind_cursor_starts_on_current_kind() -> None:
    chooser, asked = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.TRAIN),
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
    chooser = opened_at_kind(kind=SydneyMode.METRO)
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
    assert build_chooser_view(chooser=chooser).trail == 'Metro · M1'
    chooser, _ = pressed(chooser=chooser, reach=None)
    view = build_chooser_view(chooser=chooser)
    assert view.trail == 'Metro · M1 · Parramatta'


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
    key = StripKey(station='S2', kind=SydneyMode.BUS, routes=('606',))
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
        origin=LINE[1], destination=None, kind=SydneyMode.BUS,
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


def test_exit_row_is_never_picked() -> None:
    """Turning the TO cursor across an exit row skips it."""
    reach = replace(REACH, rows=(
        *REACH.rows[:3], row('X1', RowKind.EXIT), *REACH.rows[3:],
    ))
    chooser, _ = turned(chooser=at_to(), steps=1, reach=reach)
    assert chooser.to_cursor == 1
    chooser, _ = turned(chooser=chooser, steps=1, reach=reach)
    assert chooser.to_cursor == 3


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
    steps = []
    while chooser is not None:
        steps.append((chooser.focus, len(chooser.columns)))
        chooser = backed(chooser=chooser)
    assert steps == [(3, 4), (2, 4), (1, 3), (0, 2)]


def test_station_first_walk_commits_line_kind() -> None:
    chooser = at_stations()
    assert chooser.columns[chooser.focus] == T1_STATIONS
    chooser, preview = turned(chooser=chooser, steps=3, reach=None)
    assert preview is None
    chooser, request = knob_pressed(chooser=chooser, reach=None)
    assert request == ColumnRequest(path=(*T1, (Level.STATION, 'E2')))
    chooser, _ = fed(chooser, request, T1_TO)
    _, pin = knob_pressed(chooser=chooser, reach=None)
    assert pin == Pin(
        origin=T1_PLACES['E2'], kind=SydneyMode.TRAIN, routes=('T1',),
    )


def test_replacement_route_commits_replacement_kind() -> None:
    chooser, _ = pressed(chooser=at_replacement_towards(), reach=None)
    chooser = with_line(chooser=chooser, towards=11, line=LINE)
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    chooser, _ = pressed(chooser=chooser, reach=None)
    _, pin = pressed(chooser=chooser, reach=REACH)
    assert pin == Pin(
        origin=LINE[1], kind=SydneyMode.REPLACEMENT, routes=('1T1',),
    )


def test_path_to_uses_choice_level() -> None:
    chooser = at_stations()
    assert path_to(chooser=chooser, index=chooser.focus)[-1] == (
        Level.STATION, 'R1',
    )
    replacements = Column(level=Level.GROUP, items=(
        Choice(
            key='replacements', label='Trackwork',
            level=Level.REPLACEMENTS,
        ),
    ))
    chooser, _ = fed(
        opened_at_kind(), ColumnRequest(path=BUS_606[:1]), replacements,
    )
    assert path_to(chooser=chooser, index=1)[-1] == (
        Level.REPLACEMENTS, 'replacements',
    )


def test_empty_column_is_never_focused() -> None:
    chooser, _ = opened(shown=Pin(origin=place('X')))
    empty = Column(level=Level.KIND, items=())
    chooser, request = with_column(chooser=chooser, path=(), column=empty)
    assert request is None
    assert chooser.columns == (empty,)
    assert pressed(chooser=chooser, reach=None) == (chooser, None)
    assert turned(chooser=chooser, steps=1, reach=None) == (chooser, None)
    chooser, preview = fed(
        opened_at_kind(), ColumnRequest(path=BUS_606[:1]),
        Column(level=Level.GROUP, items=()),
    )
    assert preview is None
    assert pressed(chooser=chooser, reach=None) == (chooser, None)


def test_replacement_place_starts_on_bus() -> None:
    chooser, request = opened(
        shown=Pin(origin=place('X'), kind=SydneyMode.REPLACEMENT),
    )
    trains_first = replace(KINDS, items=KINDS.items[::-1])
    chooser, _ = fed(chooser, request, trains_first)
    assert chooser.cursors == (2,)


def test_to_press_off_the_strip_commits_no_destination() -> None:
    chooser, _ = turned(chooser=at_to(), steps=2, reach=REACH)
    shrunk = replace(REACH, rows=REACH.rows[:3])
    _, pin = pressed(chooser=chooser, reach=shrunk)
    assert isinstance(pin, Pin)
    assert pin.destination is None
    _, pin = pressed(chooser=replace(chooser, to_cursor=-1), reach=REACH)
    assert isinstance(pin, Pin)
    assert pin.destination is None
