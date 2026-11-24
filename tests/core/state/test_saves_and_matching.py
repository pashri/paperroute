"""Tests for state: saves and matching."""

from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute_device.core.kit_effects import SaveState, Stop
from paperroute_device.core.kit_events import QuitRequested, Tick
from paperroute_device.core.kit_state import SAVE_WAIT
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import START, departure, place
from paperroute_testkit.core.kit_steps import app_of, kit_of, kit_step, step
from paperroute_testkit.core.steps import last_saved, save_of, written
from paperroute_transit.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    Event,
    LoadDepartures,
    MatchPlaces,
    PlacesFailed,
    PlacesMatched,
    TimetableSeen,
)
from paperroute_transit.core.model import Pin
from paperroute_transit.core.places import shown, stops_to_match
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import (
    LIVE_VIEWS,
    LiveView,
    Saved,
    default_settings,
    seeded,
)
from paperroute_transit.core.sides import ParentKerb, ParentKerbs
from paperroute_transit.core.state_views import saved_of
from paperroute_transit.core.stop_view import StopView
from tests.core.state.helpers import (
    ELSEWHERE,
    HOLD,
    PIN,
    PINS,
    PRESS,
    at,
    effects_of,
    loaded,
    pinned,
    saves,
    top,
    turned,
)


def test_save_waits_three_seconds_after_last_change() -> None:
    """A change is saved once, on the first tick 3 s after it."""
    kit = kit_of(state=pinned())
    for event in (at(seconds=10), HOLD, at(seconds=11), top(steps=1)):
        kit, _ = kit_step(kit=kit, event=event)
    kit, effects = kit_step(kit=kit, event=at(seconds=13))
    assert not saves(effects=effects)
    kit, effects = kit_step(kit=kit, event=at(seconds=11 + 3))
    state = app_of(kit=kit)
    assert saves(effects=effects) == [save_of(state=state)]
    assert last_saved(kit=kit) == saved_of(state=state)
    _, effects = kit_step(kit=kit, event=at(seconds=20))
    assert not saves(effects=effects)
    assert SAVE_WAIT == timedelta(seconds=3)


def test_first_tick_saves_when_never_stored() -> None:
    """With no file yet, the first tick writes one."""
    saved = seeded(pin=PIN, city=SYDNEY)
    state = initial_state(saved=saved, now=START, city=SYDNEY)
    kit = kit_of(state=state, stored=False)
    assert kit.saved is None
    kit, effects = kit_step(kit=kit, event=Tick(now=START))
    assert [written(save=save) for save in saves(effects=effects)] == [saved]
    _, effects = kit_step(kit=kit, event=at(seconds=60))
    assert not saves(effects=effects)


def test_no_save_without_change() -> None:
    """Ticks, scrolling, loads and views save only the call count."""
    state, _ = turned(state=loaded(state=pinned(), count=20), steps=2)
    for _ in LIVE_VIEWS:
        state, _ = step(state, PRESS)
    base = saved_of(state=state)
    kit = kit_of(state=state)
    writes: list[SaveState] = []
    for seconds in range(0, 3600, 5):
        kit, effects = kit_step(kit=kit, event=at(seconds=seconds))
        writes.extend(saves(effects=effects))
    assert writes
    assert len(writes) <= 6
    for save in writes:
        assert replace(written(save=save), quota=base.quota) == base


def test_quit_saves_unsaved_changes_first() -> None:
    """Quitting with an unsaved change saves before stopping."""
    kit, _ = kit_step(kit=kit_of(state=pinned()), event=HOLD)
    kit, effects = kit_step(kit=kit, event=QuitRequested())
    state = app_of(kit=kit)
    assert effects == (save_of(state=state), Stop())
    assert not state.running


def test_new_timetable_version_matches_every_place() -> None:
    """A new version re-matches every place and reloads departures."""
    far = place('far', name='Far', point=(5.0, 5.0))
    pins = (replace(PINS[0], destination=far), *PINS[1:])
    saved = Saved(
        settings=default_settings(city=SYDNEY),
        pins=pins,
        current=0,
        unpinned=ELSEWHERE,
        view=LiveView.STOP,
    )
    state = initial_state(saved=saved, now=START, city=SYDNEY)
    state = loaded(state=state)
    state, effects = step(state, TimetableSeen(version='v2'))
    origins = tuple(pin.origin for pin in PINS)
    assert MatchPlaces(
        places=(origins[0], far, *origins[1:], ELSEWHERE.origin),
        timetable='v2',
    ) in effects
    assert state.saving.timetable == 'v2'
    assert state.board.loaded_until is None
    _, effects = step(state, Tick(now=START))
    assert effects_of(effects=effects, kind=LoadDepartures)
    assert step(state, TimetableSeen(version='v2')) == (state, ())


def test_failed_match_is_retried_after_retry_after() -> None:
    """A failed place look-up asks again on the first tick 30 s later."""
    state, _ = step(loaded(state=pinned()), TimetableSeen(version='v2'))
    state, effects = step(state, PlacesFailed(timetable='v2'))
    assert not effects
    _, effects = step(state, at(seconds=25))
    assert not effects_of(effects=effects, kind=MatchPlaces)
    state, effects = step(state, at(seconds=30))
    assert effects_of(effects=effects, kind=MatchPlaces) == [
        MatchPlaces(places=stops_to_match(state=state), timetable='v2')
    ]
    _, effects = step(state, at(seconds=90))
    assert not effects_of(effects=effects, kind=MatchPlaces)


def test_failed_match_from_an_old_timetable_is_ignored() -> None:
    """A failure for another timetable version schedules no retry."""
    state, _ = step(loaded(state=pinned()), TimetableSeen(version='v2'))
    after, effects = step(state, PlacesFailed(timetable='v1'))
    assert (after, effects) == (state, ())


def test_new_timetable_cancels_a_match_retry() -> None:
    """A new version matches at once and drops the pending retry."""
    state, _ = step(loaded(state=pinned()), TimetableSeen(version='v2'))
    state, _ = step(state, PlacesFailed(timetable='v2'))
    state, effects = step(state, TimetableSeen(version='v3'))
    assert effects_of(effects=effects, kind=MatchPlaces)
    assert state.saving.match_failed_at is None


def test_unmatched_shown_pin_says_stop_not_in_the_new_timetable() -> None:
    """A missing shown pin's Stop view says it is not in the timetable."""
    state = loaded(state=pinned(), count=0)
    state, _ = step(state, PlacesMatched(found={'first': None}))
    assert state.places.pins[0].missing
    assert isinstance(state.last_view, StopView)
    assert state.last_view.empty == 'Stop not in the new timetable'


def test_matched_id_change_updates_pin_and_reloads() -> None:
    """A re-match onto a new ID shows the new stop and loads it."""
    moved = place('first-2', name='first', point=(105.0, 0.0))
    state = loaded(state=pinned(view=LiveView.MAP))
    state, effects = step(state, PlacesMatched(found={'first': moved}))
    assert shown(state=state) == Pin(origin=moved)
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == Pin(origin=moved)
    assert state.board.departures == ()
    assert state.map.centre == moved.point


def test_matched_parent_pin_turns_both_sides_and_reloads() -> None:
    """A re-match onto a parent's kerbs makes the pin both-sides."""
    state = loaded(state=pinned())
    kerbs = ParentKerbs(
        station='first',
        kerbs=(
            ParentKerb(
                place=place('first-a', point=(100.0, 0.0)),
                onward=frozenset({'n'}), trips=5,
            ),
            ParentKerb(
                place=place('first-b', point=(100.0, 30.0)),
                onward=frozenset({'s'}), trips=3,
            ),
        ),
        busy=False,
    )
    first = shown(state=state).origin
    state, effects = step(
        state,
        PlacesMatched(found={'first': first}, parents={'first': kerbs}),
    )
    pin = shown(state=state)
    assert (pin.origin.stop_id, pin.opposite) == (
        'first-a', kerbs.kerbs[1].place,
    )
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == pin


def test_matched_parent_a_second_time_changes_nothing() -> None:
    """The same re-match twice leaves the state and asks for nothing."""
    state = loaded(state=pinned())
    kerbs = ParentKerbs(
        station='first',
        kerbs=(
            ParentKerb(
                place=place('first-a', point=(100.0, 0.0)),
                onward=frozenset({'n'}), trips=5,
            ),
            ParentKerb(
                place=place('first-b', point=(100.0, 30.0)),
                onward=frozenset({'s'}), trips=3,
            ),
        ),
        busy=False,
    )
    event = PlacesMatched(
        found={'first': shown(state=state).origin}, parents={'first': kerbs},
    )
    state, _ = step(state, event)
    assert step(state, event) == (state, ())


def test_matched_name_only_refresh_keeps_the_board() -> None:
    """A re-match under the same ID keeps the departures loaded."""
    renamed = replace(PINS[0].origin, name='First St')
    state = loaded(state=pinned())
    before = state.board
    state, effects = step(state, PlacesMatched(found={'first': renamed}))
    assert state.board == before
    assert not effects_of(effects=effects, kind=LoadDepartures)
    assert isinstance(state.last_view, StopView)
    assert state.last_view.origin == 'First St'


def test_no_timetable_matches_nothing() -> None:
    """With no timetable there is nothing to match against."""
    state, _ = step(pinned(), TimetableSeen(version='v1'))
    state, effects = step(state, TimetableSeen(version=None))
    assert not effects_of(effects=effects, kind=MatchPlaces)
    assert state.saving.timetable is None


@pytest.mark.parametrize('failed', [False, True])
def test_load_from_the_old_timetable_is_dropped(failed: bool) -> None:
    """A reply to a load asked before a timetable swap is ignored."""
    state, _ = step(pinned(), TimetableSeen(version='v1'))
    state, effects = step(state, at(seconds=5))
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.timetable == 'v1'
    state, _ = step(state, TimetableSeen(version='v2'))
    late = departure(departs=START + timedelta(minutes=3))
    reply: Event = DeparturesLoaded(
        pin=load.pin, departures=(late,), until=load.end, timetable='v1'
    )
    if failed:
        reply = DeparturesFailed(pin=load.pin, timetable='v1')
    state, _ = step(state, reply)
    assert state.board.loaded_until is None
    assert state.board.departures == ()
    _, effects = step(state, at(seconds=10))
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.timetable == 'v2'
