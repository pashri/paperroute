"""Tests for invariants: step invariants."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute_device.core.kit_events import Tick
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.kit_steps import app_of, kit_of, kit_step, step
from paperroute_testkit.core.steps import last_saved
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    LoadShapes,
)
from paperroute_transit.core.places import shown
from paperroute_transit.core.realtime import (
    RealtimeSnapshot,
    StopUpdate,
    TripRealtime,
)
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LiveView, Saved, default_saved
from paperroute_transit.core.stop_view import HORIZON, StopView
from tests.core.state.helpers import ALERTS, BUSES, PIN, effects_of, fresh
from tests.core.state.invariant_checks import (
    check_effects,
    check_places,
    check_saves,
    start_saved,
)
from tests.core.state.invariant_drivers import WindowedTimetable, run_tick
from tests.core.state.invariant_events import (
    FALL_BACK_EVE,
    Answer,
    answered,
    event_lists,
)


@pytest.mark.parametrize('view', [LiveView.STRIP, LiveView.STOP])
def test_step_invariants_asleep_on_a_view_without_a_map(
    view: LiveView,
) -> None:
    """Falling asleep off the Map loads the sleep map, not positions."""
    night = datetime(2026, 4, 12, 1, 30, tzinfo=SYDNEY_ZONE)
    state = initial_state(
        saved=replace(
            default_saved(city=SYDNEY),
            view=view,
        ), now=night,
        city=SYDNEY,
    )
    trip = replace(departure(departs=night), shape_id='b:1')
    state, _ = step(
        state,
        DeparturesLoaded(
            pin=shown(state=state),
            departures=(trip,),
            until=night + HORIZON,
        ),
    )
    tick = Tick(now=night)
    after, effects = step(state, tick)
    assert after.panel.asleep
    assert effects_of(effects=effects, kind=LoadShapes)
    assert not effects_of(effects=effects, kind=FetchFeed)
    check_effects(before=state, after=after, event=tick, effects=effects)


@given(
    st.sampled_from([START, FALL_BACK_EVE]).flatmap(
        lambda start: st.tuples(st.just(start), event_lists(start))
    ),
    start_saved(),
    st.booleans(),
)
def test_step_invariants(
    case: tuple[datetime, list[Event | Answer]], saved: Saved, stored: bool
) -> None:
    """No event sequence breaks the reducer's invariants."""
    start, events = case
    state = initial_state(saved=saved, now=start, city=SYDNEY)
    kit = kit_of(state=state, stored=stored)
    last = last_saved(kit=kit)
    for item in events:
        event = (
            answered(state=state, answer=item)
            if isinstance(item, Answer)
            else item
        )
        before = kit
        kit, effects = kit_step(kit=before, event=event)
        after = app_of(kit=kit)
        check_effects(
            before=state, after=after, event=event, effects=effects
        )
        check_places(before=state, after=after, event=event)
        last = check_saves(
            before=before, after=kit, effects=effects, last=last
        )
        state = after


def test_empty_never_shows_while_a_departure_is_within_horizon() -> None:
    """Across many load cycles the list always reaches a full horizon."""
    only = departure(departs=START + timedelta(hours=5, minutes=30))
    timetable = WindowedTimetable(departures=(only,))
    state = fresh()
    for minute in range(0, 6 * 60 + 1):
        now = START + timedelta(minutes=minute)
        state = run_tick(state=state, timetable=timetable, now=now)
        assert isinstance(state.last_view, StopView)
        in_reach = only.departs <= now + HORIZON
        if in_reach and only.departs >= now:
            assert state.last_view.empty is None, now


def test_late_service_timetabled_before_now_appears_after_load() -> None:
    """A service timetabled 2 min ago but 6 min late is still listed."""
    late = departure(departs=START - timedelta(minutes=2))
    timetable = WindowedTimetable(departures=(late,))
    trip = TripRealtime(
        trip_id=late.trip_id,
        cancelled=False,
        updates=(
            StopUpdate(
                sequence=late.origin_seq,
                stop_id=None,
                arrival_delay=None,
                departure_delay=360,
                arrival_time=None,
                departure_time=None,
            ),
        ),
        start_date=None,
    )
    state = run_tick(
        state=fresh(), timetable=timetable, now=START - timedelta(seconds=30),
    )
    state, _ = step(state, Tick(now=START))
    snapshot = RealtimeSnapshot(fetched_at=START, trips={late.trip_id: trip})
    state, _ = step(state, FeedFetched(feed=BUSES, snapshot=snapshot))
    state, _ = step(state, FeedFailed(feed=ALERTS, at=START, reason='test'))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.hero is not None
    assert state.last_view.hero.leaves_at == '8:04'


def test_view_says_loading_before_the_first_load() -> None:
    """Until departures have loaded, the empty text says so."""
    state, _ = step(fresh(), Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.empty == 'Loading timetable'
    loaded_state, _ = step(
        state,
        DeparturesLoaded(pin=PIN, departures=(), until=START + HORIZON),
    )
    assert isinstance(loaded_state.last_view, StopView)
    assert loaded_state.last_view.empty is not None
    assert 'No direct services' in loaded_state.last_view.empty
