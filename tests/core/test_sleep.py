"""Tests for sleeping the screen from the Settings menu."""

from dataclasses import replace
from datetime import timedelta

from paperroute.core.device_model import ConfirmAction, MenuRow
from paperroute.core.geo import make_shape
from paperroute.core.map_view import MAP_HEIGHT, MapView, screen_point
from paperroute.core.messages import (
    ApiKeyChanged,
    ConfirmAsked,
    DeparturesLoaded,
    DeviceProbed,
    Effect,
    Event,
    FetchFeed,
    Knob,
    KnobHeld,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
    LoadPatterns,
    LoadShapes,
    PatternsLoaded,
    Render,
    ShapesLoaded,
    Tick,
    TimetableSeen,
)
from paperroute.core.panel_views import ConfirmView, SleepView
from paperroute.core.saved import seeded
from paperroute.core.sleep import SLEEP_BAND, sleep_view, slept
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.state_views import live_stop_view
from paperroute.core.stop_view import StopView
from paperroute.render.sleep import BAND_H
from paperroute.render.trip_marks import END_DISC, END_EDGE
from tests.core.factories import START, departure, pattern, place
from tests.core.test_connection import loaded as connection_loaded
from tests.core.test_menu import BOTH, DOWN, run, turn
from tests.core.test_state import BUSES, CENTRE, PIN, fresh, loaded
from tests.fakes import device_facts


def asleep() -> State:
    """
    Sleep a loaded state through the menu's Sleep now row.

    Returns
    -------
    State
        The state after the row was pressed.
    """
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(BOTH, turn(steps=index)))
    return step(state, DOWN)[0]


def renders(*, effects: tuple[Effect, ...]) -> list[object]:
    """
    List the views drawn.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[object]
        Each Render's view.
    """
    return [e.view for e in effects if isinstance(e, Render)]


def test_sleep_row_sleeps() -> None:
    """Sleep now draws the map with no markers under the sleep band."""
    state, _ = step(loaded(state=fresh()), Tick(now=START))
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(BOTH, turn(steps=index)))
    state, effects = step(state, DOWN)
    assert state.panel.asleep
    assert state.panel.menu is None
    (view,) = renders(effects=effects)
    assert isinstance(view, SleepView)
    assert view.band == SLEEP_BAND
    assert view.map.markers == ()
    assert view.map.sidebar == ()
    assert isinstance(view.map, MapView)


def test_no_polls_or_redraws_while_asleep() -> None:
    """Five minutes of ticks fetch nothing and draw nothing."""
    state = asleep()
    for seconds in range(10, 301, 10):
        state, effects = step(
            state, Tick(now=START + timedelta(seconds=seconds)),
        )
        assert not [e for e in effects if isinstance(e, FetchFeed)]
        assert not renders(effects=effects)


def test_first_touch_only_wakes() -> None:
    """A top turn while asleep leaves the pin and shows the Live view."""
    state = asleep()
    before = state.places
    state, effects = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert not state.panel.asleep
    assert state.places == before
    (view,) = renders(effects=effects)
    assert isinstance(view, StopView)


def test_wake_polls_at_once() -> None:
    """Waking fetches the feeds in the same step."""
    state, _ = step(asleep(), Tick(now=START + timedelta(seconds=10)))
    woken, effects = step(state, KnobPressed(knob=Knob.BOTTOM))
    assert woken.last_fetch is not None
    assert FetchFeed(feed=BUSES) in effects


def test_both_held_while_asleep_only_wakes() -> None:
    """Holding both knobs wakes without opening the menu."""
    state, effects = step(asleep(), KnobsHeld())
    assert not state.panel.asleep
    assert state.panel.menu is None
    assert isinstance(renders(effects=effects)[0], StopView)


def test_hold_while_asleep_only_wakes() -> None:
    """A bottom hold wakes and does not pin."""
    state = asleep()
    woken, _ = step(state, KnobHeld(knob=Knob.BOTTOM))
    assert not woken.panel.asleep
    assert woken.places == state.places


def test_phone_confirm_wakes() -> None:
    """A phone's reset request wakes the screen and shows the confirm."""
    state, effects = step(
        asleep(), ConfirmAsked(action=ConfirmAction.RESET_SETTINGS),
    )
    assert not state.panel.asleep
    assert state.panel.confirm is not None
    views = renders(effects=effects)
    assert len(views) == 1
    assert isinstance(views[0], ConfirmView)
    assert FetchFeed(feed=BUSES) in effects


def test_ignored_ask_while_asleep_still_wakes() -> None:
    """An ask the panel won't show still wakes to the Live view."""
    state, effects = step(
        asleep(), ConfirmAsked(action=ConfirmAction.RESTART),
    )
    assert not state.panel.asleep
    assert isinstance(renders(effects=effects)[0], StopView)



def test_sleep_screen_is_drawn_once() -> None:
    """Events that change the board, key or facts draw nothing asleep."""
    state = asleep()
    for count in (1, 5):
        deps = tuple(
            departure(departs=START + timedelta(minutes=3 * (i + 1)))
            for i in range(count)
        )
        events: tuple[Event, ...] = (
            DeparturesLoaded(
                pin=state.places.pins[0],
                departures=deps,
                until=START + timedelta(hours=2),
            ),
            DeviceProbed(facts=device_facts(key_set=count == 5)),
            TimetableSeen(version=f'v{count}'),
            ApiKeyChanged(present=count == 1),
            Tick(now=START + timedelta(minutes=count)),
            Tick(now=START + timedelta(minutes=count, seconds=40)),
        )
        for event in events:
            state, effects = step(state, event)
            assert not renders(effects=effects)
    assert state.panel.asleep
    state, effects = step(state, KnobPressed(knob=Knob.BOTTOM))
    assert isinstance(renders(effects=effects)[0], StopView)


def test_sleep_screen_redrawn_once_departures_load() -> None:
    """A sleep screen drawn while loading is drawn again once, loaded."""
    state, _ = step(fresh(), Tick(now=START))
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(BOTH, turn(steps=index)))
    state, effects = step(state, DOWN)
    (first,) = renders(effects=effects)
    assert isinstance(first, SleepView)
    assert first.loading
    event = DeparturesLoaded(
        pin=state.places.pins[0],
        departures=(departure(departs=START + timedelta(minutes=3)),),
        until=START + timedelta(hours=2),
    )
    state, effects = step(state, event)
    (second,) = renders(effects=effects)
    assert isinstance(second, SleepView)
    assert not second.loading
    _, effects = step(state, event)
    assert not renders(effects=effects)


def test_connection_sleep_screen_draws_the_next_connection() -> None:
    """A connection pin sleeps on its connection map, not direct trips."""
    state = slept(state=connection_loaded())
    view = sleep_view(state=state, stop_view=live_stop_view(state=state))
    assert view.map.empty is None
    assert view.map.base.route is not None
    assert view.map.pairs == ()
    empty = slept(state=connection_loaded(second=()))
    gone = sleep_view(state=empty, stop_view=live_stop_view(state=empty))
    assert gone.map.empty is not None
    assert gone.map.empty.startswith('No connections')


def test_sleep_map_keeps_both_ends_above_the_band() -> None:
    """A trip running down the map ends with its To disc clear of the band."""
    pin = replace(
        PIN,
        origin=replace(PIN.origin, point=(0.0, 900.0)),
        destination=place('215020', point=(0.0, -900.0)),
    )
    state = slept(
        state=initial_state(saved=seeded(pin=pin), now=START, stored=True),
    )
    view = sleep_view(state=state, stop_view=live_stop_view(state=state))
    base = view.map.base
    assert base.centre is not None and base.trip is not None
    for point in (base.trip.origin, base.trip.end):
        assert point is not None
        _, y = screen_point(
            centre=base.centre, mpp=base.mpp, point=point, width=base.width,
        )
        assert y + END_DISC + END_EDGE <= MAP_HEIGHT - BAND_H


def test_sleep_screen_waits_for_its_route_line_and_stops() -> None:
    """Asleep, the map asks for its trip and is drawn again once it loads."""
    state, _ = step(fresh(centre=CENTRE), Tick(now=START))
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    state, _ = run(state=state, events=(BOTH, turn(steps=index)))
    state, _ = step(state, DOWN)
    trip = replace(
        departure(departs=START + timedelta(minutes=3)), shape_id='b:0',
    )
    state, effects = step(
        state,
        DeparturesLoaded(
            pin=state.places.pins[0],
            departures=(trip,),
            until=START + timedelta(hours=2),
        ),
    )
    assert LoadShapes(shape_ids=frozenset({'b:0'})) in effects
    assert [e for e in effects if isinstance(e, LoadPatterns)]
    line = make_shape(shape_id='b:0', points=[CENTRE, (-16_900.0, 1_000.0)])
    state, _ = step(state, ShapesLoaded(shapes={'b:0': line}))
    state, effects = step(
        state,
        PatternsLoaded(
            trip_ids=frozenset({trip.trip_id}),
            patterns={trip.trip_id: pattern(trip_id=trip.trip_id)},
            shapes={},
        ),
    )
    (view,) = renders(effects=effects)
    assert isinstance(view, SleepView)
    assert not view.loading
    assert view.map.base.route == (line.points,)
    assert view.map.base.stops == ((1000.0, 0.0),)
    assert view.map.markers == ()
    _, effects = step(state, Tick(now=START + timedelta(seconds=40)))
    assert not renders(effects=effects)
