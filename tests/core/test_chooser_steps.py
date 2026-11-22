"""Tests for the route chooser in the reducer."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_device.render.canvas import HEIGHT, WIDTH
from paperroute_hardware.core.knob_events import Knob, KnobHeld, KnobTurned
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    begun,
    calls,
    departure,
    pattern,
    pin_at,
    place,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_transit.core.chooser import ChooserState, Failure, Level, Stage
from paperroute_transit.core.chooser_lists import preview_steps
from paperroute_transit.core.chooser_steps import (
    DEPARTURES_FAILED,
    LINE_FAILED,
    from_inputs,
)
from paperroute_transit.core.chooser_view import LOADING_LISTS, ChooserView
from paperroute_transit.core.map_view import DEFAULT_ZOOM
from paperroute_transit.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    DeparturesLoaded,
    Effect,
    Event,
    LineFailed,
    LineLoaded,
    LoadChooserDepartures,
    LoadColumn,
    LoadDepartures,
    LoadLine,
    LoadPatterns,
    LoadPreviews,
    LoadStrip,
    PatternsLoaded,
    PreviewsFailed,
    PreviewsLoaded,
    StripLoaded,
    TimetableSeen,
)
from paperroute_transit.core.model import Departure, Pin, shifted
from paperroute_transit.core.places import shown
from paperroute_transit.core.saved import LiveView, Saved, default_settings
from paperroute_transit.core.sides import KerbPair
from paperroute_transit.core.state_model import LOOKBACK, RELOAD_BEFORE, State
from paperroute_transit.core.state_views import saved_of
from paperroute_transit.core.stop_view import HORIZON, StopView
from paperroute_transit.core.strip_model import HINTS, StripView
from paperroute_transit.render.strip_view import draw_strip_view
from tests.core.chooser_walks import (
    BUS,
    BUS_606,
    CHOSEN,
    CHOSEN_KEY,
    DOWN,
    GROUPS,
    KINDS,
    LINE,
    NEXT,
    OPEN,
    REACH,
    ROUTES,
    TO_STAGE,
    UNTIL,
    chooser_departures,
    previews_of,
)
from tests.core.test_chooser_lists import ASKING

PINS: Final[tuple[Pin, ...]] = (
    pin_at('first', x=100.0),
    pin_at('second', x=200.0),
    pin_at('third', x=300.0),
)
HOLD: Final[KnobHeld] = KnobHeld(knob=Knob.BOTTOM)


def pinned(*, view: LiveView = LiveView.STOP) -> State:
    """
    A state with three pins, the first shown, drawn once.

    Parameters
    ----------
    view : LiveView
        The Live view shown.

    Returns
    -------
    State
        The state after its first tick.
    """
    saved = Saved(
        settings=default_settings(city=SYDNEY),
        pins=PINS, current=0, unpinned=None, view=view,
    )
    state = begun(saved=saved)
    state, _ = step(state, Tick(now=START))
    return state


def loaded(*, state: State) -> State:
    """
    Load three buses for the place shown.

    Parameters
    ----------
    state : State
        State before.

    Returns
    -------
    State
        State after.
    """
    departures = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=departures,
        until=START + HORIZON + RELOAD_BEFORE,
    )
    return step(state, event)[0]


def effects_of[T](
    *, effects: tuple[Effect, ...], kind: type[T]
) -> list[T]:
    """
    Pick the effects of one type.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.
    kind : type[T]
        Effect type.

    Returns
    -------
    list[T]
        The matching effects, in order.
    """
    return [effect for effect in effects if isinstance(effect, kind)]


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


def top(*, steps: int) -> KnobTurned:
    """
    Turn the top knob.

    Parameters
    ----------
    steps : int
        Detents.

    Returns
    -------
    KnobTurned
        The turn.
    """
    return KnobTurned(knob=Knob.TOP, steps=steps)


def picked(
    *, departures: tuple[Departure, ...] | None = None,
) -> ChooserDeparturesLoaded:
    """
    The departures from the chosen From, loaded.

    Parameters
    ----------
    departures : tuple[Departure, ...] | None
        Departures; `chooser_departures` by default.

    Returns
    -------
    ChooserDeparturesLoaded
        The reply.
    """
    return ChooserDeparturesLoaded(
        pin=CHOSEN,
        departures=(
            chooser_departures() if departures is None else departures
        ),
        until=UNTIL,
    )


def test_top_press_opens_chooser_and_asks_kind_column() -> None:
    """The top press opens the chooser and asks for the KIND column."""
    before = pinned()
    state, effects = step(before, OPEN)
    assert state.chooser == ChooserState(waiting=(), prefer=None)
    assert effects_of(effects=effects, kind=LoadColumn) == [
        LoadColumn(path=()),
    ]
    [render] = effects_of(effects=effects, kind=Render)
    assert isinstance(render.view, ChooserView)
    assert render.view.empty == LOADING_LISTS
    assert saved_of(state=state) == saved_of(state=before)


def test_chooser_ignores_top_turns_and_holds() -> None:
    """While the chooser is open, the top turn and both holds do nothing."""
    state, _ = step(pinned(), OPEN)
    for event in (top(steps=1), HOLD, KnobHeld(knob=Knob.TOP)):
        assert step(state, event) == (state, ())


def test_back_from_kind_closes_without_change() -> None:
    """Going back from the KIND column closes the chooser, nothing changed."""
    before = pinned()
    state, _ = run_events(
        state=before,
        events=[OPEN, ColumnLoaded(path=(), column=KINDS), DOWN],
    )
    assert state.chooser is not None
    state, effects = step(state, OPEN)
    assert state.chooser is None
    assert (state.places, state.view) == (before.places, before.view)
    [render] = effects_of(effects=effects, kind=Render)
    assert render.view == before.last_view


def test_from_stage_draws_the_line() -> None:
    """The FROM stage is a strip of the towards line with no panel rows."""
    state, _ = run_events(state=loaded(state=pinned()), events=TO_STAGE[:-2])
    view = state.last_view
    assert isinstance(view, StripView)
    assert (view.picking, view.panel.rows) == ('FROM', ())
    assert view.panel.hint == HINTS['FROM']
    assert (view.origin, view.destination) == ('606 to Parramatta', None)
    assert (view.panel.title, view.panel.destination) == (
        '606 to Parramatta', None,
    )
    assert [row.name for row in view.rows] == [
        'Parramatta', 'Northmead', 'Baulkham Hills',
    ]
    assert view.rows[0].cursor


def test_from_stage_says_when_the_line_fails() -> None:
    """A line that can't be read leaves a short note on the FROM stage."""
    state, _ = run_events(state=pinned(), events=TO_STAGE[:-3])
    state, _ = step(state, LineFailed(towards=11))
    assert isinstance(state.last_view, StripView)
    assert state.last_view.empty == LINE_FAILED


def test_from_stage_view_carries_the_city_name_rules() -> None:
    """The FROM strip fits names with the city's own rules."""
    state, _ = run_events(state=loaded(state=pinned()), events=TO_STAGE[:-2])
    assert state.chooser is not None
    inputs = from_inputs(state=state, chooser=state.chooser)
    assert inputs.stop_view.name_rules is state.city.name_rules


def test_to_stage_asks_for_the_strip_and_departures() -> None:
    """Pressing a From asks for its strip and its own departures."""
    state, effects = run_events(state=pinned(), events=TO_STAGE)
    assert state.chooser is not None
    assert state.chooser.stage is Stage.TO
    assert effects_of(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=CHOSEN_KEY, origin=CHOSEN.origin),
    ]
    [load] = effects_of(effects=effects, kind=LoadChooserDepartures)
    assert load.pin == CHOSEN
    assert (load.start, load.end) == (
        START - LOOKBACK,
        START + HORIZON + RELOAD_BEFORE,
    )
    assert effects_of(effects=effects, kind=LoadColumn) == [
        LoadColumn(path=()),
    ]
    assert [
        load.path for load in effects_of(effects=effects, kind=LoadPreviews)
    ] == [(), BUS, BUS_606[:2]]
    assert effects_of(effects=effects, kind=LoadLine) == [
        LoadLine(towards=11),
    ]


def test_to_stage_panel_shows_the_chosen_from() -> None:
    """In the TO stage the panel and vehicles are the chooser's From's."""
    state, _ = run_events(state=loaded(state=pinned()), events=TO_STAGE)
    state, effects = run_events(
        state=state,
        events=[
            StripLoaded(key=CHOSEN_KEY, reach=REACH),
            picked(),
        ],
    )
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert load.trip_ids == {'c-0', 'c-1'}
    trip = pattern(
        trip_id='c-0',
        stops=calls(stations=['S2', 'S3', 'S4'], start=EIGHT - 60),
    )
    state, _ = step(
        state,
        PatternsLoaded(
            trip_ids=load.trip_ids, patterns={'c-0': trip}, shapes={},
        ),
    )
    view = state.last_view
    assert isinstance(view, StripView)
    assert (view.picking, view.origin, view.panel.title) == (
        'TO', 'Northmead', 'Northmead',
    )
    assert [row.destination for row in view.panel.rows] == ['Hawkesbury']
    assert [row.cursor for row in view.rows] == [False, True, False, False]
    [marker] = view.markers
    assert marker.route == '606'
    assert shown(state=state) == PINS[0]


def test_stale_chooser_departures_are_ignored() -> None:
    """Departures for a From no longer chosen are dropped."""
    state, _ = run_events(
        state=pinned(), events=[*TO_STAGE, OPEN, DOWN, NEXT],
    )
    assert state.chooser is not None
    assert state.chooser.origin == LINE[2]
    for event in (
        picked(),
        replace(picked(), pin=replace(CHOSEN, origin=LINE[2]), timetable='v1'),
    ):
        assert step(state, event) == (state, ())


def test_commit_hands_departures_to_the_new_place() -> None:
    """A From-only commit starts the Stop view with the chooser's board."""
    state, _ = run_events(
        state=pinned(view=LiveView.MAP),
        events=[
            *TO_STAGE,
            StripLoaded(key=CHOSEN_KEY, reach=REACH),
            picked(),
        ],
    )
    state, effects = step(state, NEXT)
    assert state.chooser is None
    assert state.board.departures == chooser_departures()
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == CHOSEN


def test_commit_makes_unpinned_place_on_stop_view() -> None:
    """A TO press makes an unpinned place and lands on the Stop view."""
    state, _ = run_events(
        state=pinned(view=LiveView.MAP),
        events=[
            *TO_STAGE,
            StripLoaded(key=CHOSEN_KEY, reach=REACH),
            picked(),
            DOWN,
        ],
    )
    state, effects = step(state, NEXT)
    chosen = replace(CHOSEN, destination=place('S3'))
    assert state.chooser is None
    assert state.places.pins == PINS
    assert state.places.unpinned == chosen
    assert state.view is LiveView.STOP
    assert state.board.departures == ()
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == chosen
    [render] = effects_of(effects=effects, kind=Render)
    assert isinstance(render.view, StopView)
    assert render.view.tag.text == 'not pinned'


def test_commit_matching_pin_selects_it() -> None:
    """Choosing a pinned place selects that pin."""
    saved = Saved(
        settings=default_settings(city=SYDNEY),
        pins=(PINS[0], CHOSEN, PINS[2]),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
    )
    state = begun(saved=saved)
    state, _ = run_events(
        state=state,
        events=[*TO_STAGE, StripLoaded(key=CHOSEN_KEY, reach=REACH), NEXT],
    )
    assert state.chooser is None
    assert state.places == replace(state.places, current=1, unpinned=None)
    assert state.places.pins == saved.pins
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'pin 2/3'


def test_nothing_persisted_changes_until_the_commit() -> None:
    """Walking the chooser and backing out changes nothing saved."""
    before = pinned(view=LiveView.STRIP)
    state, _ = run_events(
        state=before,
        events=[
            *TO_STAGE,
            StripLoaded(key=CHOSEN_KEY, reach=REACH),
            DOWN,
            OPEN,
            OPEN,
            OPEN,
            OPEN,
            OPEN,
            OPEN,
        ],
    )
    assert state.chooser is None
    assert saved_of(state=state) == saved_of(state=before)


def test_new_timetable_asks_the_chooser_again() -> None:
    """A new timetable asks again for the line or the TO stage's loads."""
    state, _ = run_events(state=pinned(), events=TO_STAGE[:-4])
    _, effects = step(state, TimetableSeen(version='v1'))
    assert not effects_of(effects=effects, kind=LoadColumn)
    state, _ = step(state, NEXT)
    _, effects = step(state, TimetableSeen(version='v1'))
    assert effects_of(effects=effects, kind=LoadLine) == [
        LoadLine(towards=11, timetable='v1'),
    ]
    state, _ = run_events(
        state=state,
        events=[
            *TO_STAGE[-3:],
            StripLoaded(key=CHOSEN_KEY, reach=REACH),
            DOWN,
        ],
    )
    assert state.chooser is not None
    assert state.chooser.to_cursor == 1
    state, effects = step(state, TimetableSeen(version='v1'))
    assert state.chooser is not None
    assert state.chooser.to_cursor == 0
    assert effects_of(effects=effects, kind=LoadStrip) == [
        LoadStrip(key=CHOSEN_KEY, origin=CHOSEN.origin, timetable='v1'),
    ]
    [load] = effects_of(effects=effects, kind=LoadChooserDepartures)
    assert (load.pin, load.timetable) == (CHOSEN, 'v1')


def test_turning_reads_nothing_once_previews_are_in() -> None:
    """A turn installs the preview it already holds and asks for nothing."""
    state, effects = run_events(
        state=pinned(), events=[OPEN, ColumnLoaded(path=(), column=KINDS)],
    )
    assert [load.path for load in effects_of(
        effects=effects, kind=LoadPreviews,
    )] == [()]
    state, _ = step(state, previews_of(path=(), column=KINDS))
    _, effects = run_events(state=state, events=[DOWN, DOWN, DOWN])
    assert not effects_of(effects=effects, kind=LoadColumn)
    assert not effects_of(effects=effects, kind=LoadPreviews)


def test_new_timetable_drops_previews_and_asks_for_the_focus() -> None:
    """A new timetable drops every column's previews and asks the focus's."""
    state, _ = run_events(state=pinned(), events=TO_STAGE[:6])
    before = state.chooser
    state, effects = step(state, TimetableSeen(version='v1'))
    chooser = state.chooser
    assert chooser is not None and before is not None
    assert [c.previews for c in chooser.columns] == [None, (), None]
    assert [c.items for c in chooser.columns] == [
        c.items for c in before.columns
    ]
    assert effects_of(effects=effects, kind=LoadPreviews) == [LoadPreviews(
        path=BUS, steps=preview_steps(column=GROUPS), timetable='v1',
    )]


@pytest.mark.parametrize('failed', [False, True])
def test_new_timetable_closes_a_failed_chooser(failed: bool) -> None:
    """A new timetable closes a failed chooser and asks again otherwise."""
    state, _ = step(pinned(), OPEN)
    if failed:
        state, _ = step(state, ColumnFailed(path=()))
    state, effects = step(state, TimetableSeen(version='v1'))
    if failed:
        assert state.chooser is None
        assert not effects_of(effects=effects, kind=LoadColumn)
    else:
        assert effects_of(effects=effects, kind=LoadColumn) == [
            LoadColumn(path=(), timetable='v1'),
        ]
        state, _ = step(state, ColumnLoaded(path=(), column=KINDS))
        assert state.chooser is not None
        assert state.chooser.waiting == ()
        state, _ = step(
            state, ColumnLoaded(path=(), column=KINDS, timetable='v1'),
        )
        assert state.chooser is not None
        assert state.chooser.columns == (replace(KINDS, previews=()),)


def test_to_panel_says_when_no_service_runs() -> None:
    """Loaded and empty, the TO panel says nothing runs, not loading."""
    state, _ = run_events(state=pinned(), events=TO_STAGE)
    assert isinstance(state.last_view, StripView)
    assert state.last_view.panel.empty == 'Loading timetable'
    state, _ = step(state, picked(departures=()))
    assert isinstance(state.last_view, StripView)
    assert state.last_view.panel.empty == 'No services in the next 3 hours'


def test_to_panel_says_when_departures_fail() -> None:
    """A failed load shows a short failure line and is tried again."""
    state, _ = run_events(state=pinned(), events=TO_STAGE)
    state, _ = step(state, ChooserDeparturesFailed(pin=CHOSEN))
    assert isinstance(state.last_view, StripView)
    assert state.last_view.panel.empty == DEPARTURES_FAILED
    _, effects = step(state, Tick(now=START + timedelta(seconds=5)))
    [load] = effects_of(effects=effects, kind=LoadChooserDepartures)
    assert load.pin == CHOSEN


def test_chooser_departures_reload_on_a_long_dwell() -> None:
    """Departures are loaded again as they run short, like the board's."""
    state, _ = run_events(
        state=pinned(), events=[*TO_STAGE, picked()],
    )
    state, effects = step(state, Tick(now=START + timedelta(minutes=10)))
    assert not effects_of(effects=effects, kind=LoadChooserDepartures)
    late = shifted(
        moment=UNTIL,
        seconds=-int(HORIZON.total_seconds()) + 1,
        zone=SYDNEY_ZONE,
    )
    state, _ = step(state, TimetableSeen(version='v1'))
    state, _ = step(state, replace(picked(), timetable='v1'))
    state, effects = step(state, Tick(now=late))
    [load] = effects_of(effects=effects, kind=LoadChooserDepartures)
    assert (load.pin, load.timetable) == (CHOSEN, 'v1')
    assert (load.start, load.end) == (
        late - LOOKBACK, late + HORIZON + RELOAD_BEFORE,
    )
    _, effects = step(state, Tick(now=late + timedelta(seconds=5)))
    assert not effects_of(effects=effects, kind=LoadChooserDepartures)


def test_no_map_patterns_in_the_to_stage_over_a_map() -> None:
    """Over the Map, the TO stage asks only for its own strip's trips."""
    state, _ = run_events(state=pinned(view=LiveView.MAP), events=TO_STAGE)
    board = DeparturesLoaded(
        pin=PINS[0],
        departures=(
            departure(
                departs=START + timedelta(minutes=2), trip_id='map-trip',
            ),
        ),
        until=START + HORIZON,
    )
    _, effects = run_events(state=state, events=[board, picked()])
    loads = effects_of(effects=effects, kind=LoadPatterns)
    assert [load.trip_ids for load in loads] == [{'c-0', 'c-1'}]


def test_chooser_replies_with_it_closed_are_ignored() -> None:
    """A reply that lands after the chooser closed changes nothing."""
    state, _ = run_events(state=pinned(), events=[OPEN, OPEN])
    assert state.chooser is None
    assert step(state, ColumnLoaded(path=(), column=KINDS)) == (state, ())


def test_from_stage_opens_the_box_on_a_paired_stop() -> None:
    """A paired From opens the box over its row, and the frame draws."""
    pair = KerbPair(
        station=LINE[1].stop_id, other=place('X2', name='Across'),
        busy=False,
    )
    events = (
        *TO_STAGE[:-3],
        LineLoaded(towards=11, line=LINE, pairs=(pair,)),
        DOWN,
        NEXT,
    )
    state, _ = run_events(state=loaded(state=pinned()), events=events)
    view = state.last_view
    assert isinstance(view, StripView)
    assert [row.name for row in view.rows] == [
        'Parramatta', 'Northmead', 'Baulkham Hills',
    ]
    assert [row.paired for row in view.rows] == [False, True, False]
    assert [row.cursor for row in view.rows] == [False, True, False]
    assert view.menu is not None and view.menu.cursor == 0
    assert draw_strip_view(view=view).shape == (HEIGHT, WIDTH)


def test_replied_takes_previews_and_drops_old_ones() -> None:
    """Previews for the open timetable attach; old ones are dropped."""
    state = replace(pinned(), chooser=ASKING)
    previews = ((), (ROUTES,))
    old = PreviewsLoaded(path=BUS, previews=previews, timetable='old')
    assert step(state, old) == (state, ())
    after, _ = step(state, PreviewsLoaded(
        path=BUS, previews=previews, timetable=state.saving.timetable,
    ))
    assert after.chooser is not None
    assert after.chooser.columns[-1] == ROUTES
    failed, _ = step(state, PreviewsFailed(
        path=BUS, timetable=state.saving.timetable,
    ))
    assert failed.chooser is not None
    assert failed.chooser.failed is Failure.LOAD
