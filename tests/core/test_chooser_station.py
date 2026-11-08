"""Tests for picking a station and a destination on one line."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute.city import SYDNEY_ZONE
from paperroute.core.chooser import (
    Choice,
    ChooserState,
    Column,
    ColumnRequest,
    Failure,
    Level,
    focusable,
    pressed,
    turned,
)
from paperroute.core.chooser_station import knob_backed, knob_pressed
from paperroute.core.chooser_steps import trip_feeds
from paperroute.core.chooser_view import (
    CANT_LOAD,
    NEEDS_UPDATE,
    ChooserView,
    build_chooser_view,
)
from paperroute.core.messages import (
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    Effect,
    Event,
    LoadChooserDepartures,
    LoadColumn,
    Tick,
    TimetableSeen,
)
from paperroute.core.places import feeds_of
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.stop_view import HORIZON
from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_testkit.core.factories import START, begun, pin_at, place
from paperroute_transit.core.model import Pin, RouteKind, shifted
from tests.core.chooser_walks import (
    NEXT,
    OPEN,
    PENRITH,
    T1,
    T1_PLACES,
    T1_STATIONS,
    T1_TO,
    T1_TO_END,
    TO_COLUMN,
    TRAIN_LINES,
    UNTIL,
    at_stations,
    at_to_column,
    chooser_departures,
    fed,
    opened_at_kind,
)

PENRITH_PATH: Final = (*T1, (Level.STATION, 'E2'))


def focused(*, chooser: ChooserState) -> Choice:
    """
    The choice under the focus's cursor.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.

    Returns
    -------
    Choice
        The choice.
    """
    column = chooser.columns[chooser.focus]
    return column.items[chooser.cursors[chooser.focus]]


def started() -> State:
    """
    A state with one pin shown, drawn once.

    Returns
    -------
    State
        The state after its first tick.
    """
    saved = Saved(
        pins=(pin_at('first', x=100.0),),
        current=0,
        unpinned=None,
        view=LiveView.MAP,
    )
    state = begun(saved=saved)
    return step(state, Tick(now=START))[0]


def run_events(
    *, state: State, events: Sequence[Event],
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply events in turn.

    Parameters
    ----------
    state : State
        State before.
    events : Sequence[Event]
        Events.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after, and every effect in order.
    """
    effects: tuple[Effect, ...] = ()
    for event in events:
        state, more = step(state, event)
        effects = (*effects, *more)
    return state, effects


def penrith_departures() -> ChooserDeparturesLoaded:
    """
    The departures from Penrith, loaded.

    Returns
    -------
    ChooserDeparturesLoaded
        The reply.
    """
    return ChooserDeparturesLoaded(
        pin=PENRITH, departures=chooser_departures(), until=UNTIL,
    )


def test_station_cursor_skips_headings() -> None:
    chooser = at_stations()
    assert focused(chooser=chooser).key == 'R1'
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    assert focused(chooser=chooser).key == 'R2'
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    assert focused(chooser=chooser).key == 'E1'
    chooser, _ = turned(chooser=chooser, steps=-9, reach=None)
    assert focused(chooser=chooser).key == 'R1'


def test_to_cursor_starts_on_from_and_skips_dim() -> None:
    chooser = at_to_column()
    assert chooser.columns[chooser.focus] == T1_TO
    assert focused(chooser=chooser).key == 'E2'
    chooser, request = turned(chooser=chooser, steps=-1, reach=None)
    assert request is None
    assert focused(chooser=chooser).key == 'R2'
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    assert focused(chooser=chooser).key == 'E2'


def test_to_cursor_with_no_pickable_rows_stays_on_from() -> None:
    chooser = at_to_column(steps=2, column=T1_TO_END)
    assert focused(chooser=chooser).key == 'E1'
    for steps in (-1, 1, -9, 9):
        after, request = turned(chooser=chooser, steps=steps, reach=None)
        assert (after, request) == (chooser, None)


def test_station_press_preloads_departures_and_asks_to_column() -> None:
    state, _ = run_events(state=started(), events=TO_COLUMN[:-2])
    state, effects = step(state, NEXT)
    assert state.chooser is not None
    assert state.chooser.origin == T1_PLACES['E2']
    assert [e for e in effects if isinstance(e, LoadColumn)] == [
        LoadColumn(path=PENRITH_PATH),
    ]
    [load] = [e for e in effects if isinstance(e, LoadChooserDepartures)]
    assert load.pin == Pin(
        origin=T1_PLACES['E2'], kind=RouteKind.TRAIN, routes=('T1',),
    )


def test_to_press_on_all_makes_place_without_destination() -> None:
    state, _ = run_events(state=started(), events=TO_COLUMN)
    state, _ = step(state, NEXT)
    assert state.chooser is None
    assert state.view is LiveView.STOP
    assert state.places.unpinned == PENRITH
    assert state.places.unpinned.origin.point == (150.3, -33.6)


def test_to_press_on_station_makes_place_with_destination() -> None:
    state, _ = run_events(
        state=started(),
        events=[*TO_COLUMN, KnobTurned(knob=Knob.BOTTOM, steps=-1), NEXT],
    )
    assert state.chooser is None
    assert state.view is LiveView.STOP
    assert state.places.unpinned == replace(
        PENRITH, destination=T1_PLACES['R2'],
    )
    assert state.places.unpinned.origin.point == (150.3, -33.6)
    assert state.places.unpinned.destination is not None
    assert state.places.unpinned.destination.point == (150.1, -33.6)


def test_back_from_to_returns_to_station_on_from() -> None:
    chooser = knob_backed(chooser=at_to_column())
    assert chooser is not None
    assert chooser.columns[chooser.focus] == T1_STATIONS
    assert len(chooser.columns) == chooser.focus + 1
    assert focused(chooser=chooser).key == 'E2'
    assert (chooser.origin, chooser.departures) == (None, ())
    assert not chooser.load.loading
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN, penrith_departures(), OPEN],
    )
    assert state.chooser is not None
    assert state.chooser.departures == ()
    assert state.chooser.origin is None


def test_station_departures_land_in_to_column() -> None:
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN, penrith_departures()],
    )
    assert state.chooser is not None
    assert state.chooser.departures == chooser_departures()
    assert not state.chooser.load.loading
    state, _ = step(state, NEXT)
    assert state.board.departures == chooser_departures()


def test_departures_landing_before_to_column_are_kept() -> None:
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN[:-1], penrith_departures()],
    )
    state, _ = step(state, TO_COLUMN[-1])
    assert state.chooser is not None
    assert state.chooser.departures == chooser_departures()


def test_to_column_polls_its_departures_feeds() -> None:
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN, penrith_departures()],
    )
    wanted = feeds_of(departures=chooser_departures())
    assert wanted
    assert set(wanted) <= set(trip_feeds(state=state))


def test_to_column_departures_reload_when_short() -> None:
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN, penrith_departures()],
    )
    late = shifted(
        moment=UNTIL,
        seconds=-int(HORIZON.total_seconds()) + 1,
        zone=SYDNEY_ZONE,
    )
    _, effects = step(state, Tick(now=late))
    [load] = [e for e in effects if isinstance(e, LoadChooserDepartures)]
    assert load.pin == PENRITH


def test_retimed_to_column_asks_departures_again() -> None:
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN, penrith_departures()],
    )
    state, effects = step(state, TimetableSeen(version='v1'))
    [load] = [e for e in effects if isinstance(e, LoadChooserDepartures)]
    assert (load.pin, load.timetable) == (PENRITH, 'v1')
    assert state.chooser is not None
    assert state.chooser.load.loading


def test_back_from_station_forgets_the_from() -> None:
    chooser, _ = knob_pressed(chooser=at_stations(), reach=None)
    assert chooser.origin is not None
    back = knob_backed(chooser=chooser)
    assert back is not None
    assert (back.origin, back.departures, back.waiting) == (None, (), None)
    assert not back.load.loading
    state, _ = run_events(
        state=started(),
        events=[*TO_COLUMN[:-1], penrith_departures(), OPEN],
    )
    assert state.chooser is not None
    assert (state.chooser.origin, state.chooser.departures) == (None, ())
    assert trip_feeds(state=state) == state.feeds
    later = shifted(
        moment=UNTIL,
        seconds=-int(HORIZON.total_seconds()) + 1,
        zone=SYDNEY_ZONE,
    )
    _, effects = step(state, Tick(now=later))
    assert not [e for e in effects if isinstance(e, LoadChooserDepartures)]


def test_other_line_reply_at_same_station_is_ignored() -> None:
    t2 = ((Level.KIND, 'train'), (Level.LINE, 't2'))
    state, _ = run_events(
        state=started(),
        events=[
            *TO_COLUMN[:-1],
            OPEN,
            KnobTurned(knob=Knob.BOTTOM, steps=1),
            ColumnLoaded(path=t2, column=T1_STATIONS),
            NEXT,
            KnobTurned(knob=Knob.BOTTOM, steps=3),
            NEXT,
        ],
    )
    assert state.chooser is not None
    assert state.chooser.origin == T1_PLACES['E2']
    assert step(state, penrith_departures()) == (state, ())
    t2_reply = replace(
        penrith_departures(), pin=replace(PENRITH, routes=('T2',)),
    )
    state, _ = step(state, t2_reply)
    assert state.chooser is not None
    assert state.chooser.departures == chooser_departures()


@pytest.mark.parametrize(
    ('event', 'note'),
    [
        (ColumnLoaded(path=T1, column=None), NEEDS_UPDATE),
        (ColumnFailed(path=T1), CANT_LOAD),
    ],
)
def test_missing_station_column_note(event: Event, note: str) -> None:
    state, _ = run_events(
        state=started(), events=[*TO_COLUMN[:6], event],
    )
    view = state.last_view
    assert isinstance(view, ChooserView)
    assert view.columns[-1].note == note


@pytest.mark.parametrize(
    'answer_column',
    [
        Column(level=Level.ROUTE, items=()),
        Column(level=Level.TO, items=()),
        T1_STATIONS,
    ],
)
def test_station_press_answered_without_to_rows_fails(
    answer_column: Column,
) -> None:
    """A station press answered by anything but a TO column with rows."""
    state, _ = run_events(
        state=started(),
        events=[
            *TO_COLUMN[:-1],
            ColumnLoaded(path=PENRITH_PATH, column=answer_column),
        ],
    )
    chooser = state.chooser
    assert chooser is not None
    assert (chooser.waiting, chooser.failed) == (None, Failure.LOAD)
    assert len(chooser.columns) == chooser.focus + 1
    view = state.last_view
    assert isinstance(view, ChooserView)
    assert view.columns[-1].note == CANT_LOAD


def test_station_turn_asks_no_preview() -> None:
    chooser = at_stations()
    for steps in (1, 2, -1):
        chooser, request = turned(chooser=chooser, steps=steps, reach=None)
        assert request is None
        assert len(chooser.columns) == chooser.focus + 1


def test_station_turn_after_press_forgets_the_from() -> None:
    chooser, _ = knob_pressed(chooser=at_stations(), reach=None)
    assert chooser.origin == T1_PLACES['R1']
    chooser, _ = turned(chooser=chooser, steps=1, reach=None)
    assert (chooser.origin, chooser.waiting) == (None, None)


def test_presses_that_pick_nothing_change_nothing() -> None:
    chooser, request = knob_pressed(chooser=at_stations(), reach=None)
    assert isinstance(request, ColumnRequest)
    assert knob_pressed(chooser=chooser, reach=None) == (chooser, None)
    assert pressed(chooser=chooser, reach=None) == (chooser, None)
    headings = replace(
        chooser,
        columns=(
            *chooser.columns[:-1],
            Column(level=Level.STATION, items=(T1_STATIONS.items[0],)),
        ),
        cursors=(*chooser.cursors[:-1], 0),
    )
    assert knob_pressed(chooser=headings, reach=None) == (headings, None)
    no_from = replace(at_to_column(), origin=None)
    assert knob_pressed(chooser=no_from, reach=None) == (no_from, None)


def test_to_view_has_hint() -> None:
    to_view = build_chooser_view(chooser=at_to_column())
    assert (to_view.title, to_view.hint) == (
        'Choose a destination',
        'Bottom: press to set To \u2022 ALL: any stop',
    )
    assert to_view.trail == 'Train · T1 · Penrith'
    station_view = build_chooser_view(chooser=at_stations())
    assert (station_view.title, station_view.hint) == (
        'Choose a station', None,
    )
    kinds_view = build_chooser_view(chooser=ChooserState())
    assert kinds_view.hint is None


ROW_KINDS: Final = st.lists(
    st.sampled_from(['heading', 'dim', 'plain']), min_size=1, max_size=8,
).filter(lambda kinds: 'plain' in kinds)


def made_rows(*, kinds: list[str], level: Level) -> Column:
    """
    Make a STATION or TO column from row kinds.

    Parameters
    ----------
    kinds : list[str]
        'heading', 'dim' or 'plain' for each row; at least one plain.
    level : Level
        STATION or TO; a STATION column draws dim rows plain, and a TO
        column tags its first plain row 'ALL'.

    Returns
    -------
    Column
        The column.
    """
    first = kinds.index('plain')
    items = tuple(
        Choice(key='', label='To X', heading=True)
        if kind == 'heading'
        else Choice(
            key=f's{index}',
            label=f'S{index}',
            dim=kind == 'dim' and level is Level.TO,
            tag='ALL' if level is Level.TO and index == first else '',
            place=place(f's{index}', name=f'S{index}'),
        )
        for index, kind in enumerate(kinds)
    )
    return Column(level=level, items=items)


def answer(
    *, chooser: ChooserState, request: object, columns: tuple[Column, ...],
) -> ChooserState:
    """
    Answer a column request from the KIND, LINE, STATION and TO columns.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    request : object
        What it asked for.
    columns : tuple[Column, ...]
        The LINE, STATION and TO columns to answer with.

    Returns
    -------
    ChooserState
        The chooser after the answer; unchanged for anything else.
    """
    if not isinstance(request, ColumnRequest) or not request.path:
        return chooser
    depth = min(len(request.path), len(columns)) - 1
    return fed(chooser, request, columns[depth])[0]


@given(
    ROW_KINDS,
    ROW_KINDS,
    st.lists(st.sampled_from(['turn', 'press', 'back']), max_size=30),
    st.lists(st.integers(-4, 4), min_size=30, max_size=30),
)
def test_station_and_to_cursors_stay_focusable(
    stations: list[str], tos: list[str], moves: list[str], steps: list[int],
) -> None:
    """Turns, presses and backs never land on a heading or dim row."""
    columns = (
        TRAIN_LINES,
        made_rows(kinds=stations, level=Level.STATION),
        made_rows(kinds=tos, level=Level.TO),
    )
    chooser = opened_at_kind(kind=RouteKind.TRAIN)
    chooser = answer(
        chooser=chooser, request=ColumnRequest(path=T1[:1]), columns=columns,
    )
    for move, detents in zip(moves, steps, strict=False):
        request: object = None
        if move == 'press':
            chooser, request = knob_pressed(chooser=chooser, reach=None)
            if isinstance(request, Pin):
                break
        elif move == 'back':
            back = knob_backed(chooser=chooser)
            if back is None:
                break
            chooser = back
        else:
            chooser, request = turned(
                chooser=chooser, steps=detents, reach=None,
            )
        chooser = answer(chooser=chooser, request=request, columns=columns)
        level = chooser.columns[chooser.focus].level
        if level in {Level.STATION, Level.TO}:
            assert focusable(choice=focused(chooser=chooser))
        if level is not Level.TO and chooser.waiting is None:
            assert chooser.origin is None
