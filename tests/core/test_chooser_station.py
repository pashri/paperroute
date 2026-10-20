"""Tests for picking a station and a destination on one line."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.chooser import (
    Choice,
    ChooserState,
    Column,
    ColumnRequest,
    Level,
    focusable,
    pressed,
    turned,
)
from paperroute.core.chooser_station import knob_backed, knob_pressed
from paperroute.core.chooser_steps import trip_feeds
from paperroute.core.chooser_view import build_chooser_view
from paperroute.core.messages import (
    ChooserDeparturesLoaded,
    Effect,
    Event,
    Knob,
    KnobTurned,
    LoadChooserDepartures,
    LoadColumn,
    Tick,
    TimetableSeen,
)
from paperroute.core.model import Pin, RouteKind, shifted
from paperroute.core.places import feeds_of
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.stop_view import HORIZON
from paperroute.core.strip_model import HINTS
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
    UNTIL,
    at_stations,
    at_to_column,
    chooser_departures,
    fed,
)
from tests.core.factories import START, pin_at

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
        zoom=12,
    )
    state = initial_state(saved=saved, now=START, stored=True)
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
    late = shifted(moment=UNTIL, seconds=-int(HORIZON.total_seconds()) + 1)
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
        'Choose a destination', HINTS['TO'],
    )
    assert to_view.trail == 'Train · T1 · Penrith'
    station_view = build_chooser_view(chooser=at_stations())
    assert (station_view.title, station_view.hint) == (
        'Choose a station', None,
    )
    kinds_view = build_chooser_view(chooser=ChooserState())
    assert kinds_view.hint is None


@given(st.lists(st.integers(-4, 4), max_size=30))
def test_station_and_to_cursors_stay_focusable(moves: list[int]) -> None:
    """Turns, presses and backs never land on a heading or dim row."""
    chooser = at_stations()
    for move in moves:
        if move == 0:
            chooser, outcome = knob_pressed(chooser=chooser, reach=None)
            if isinstance(outcome, ColumnRequest):
                chooser, _ = fed(chooser, outcome, T1_TO)
            if isinstance(outcome, Pin):
                break
        elif move == 4 and chooser.focus > len(T1):
            chooser = knob_backed(chooser=chooser) or chooser
        else:
            chooser, _ = turned(chooser=chooser, steps=move, reach=None)
        if chooser.focus >= len(T1):
            assert focusable(choice=focused(chooser=chooser))
