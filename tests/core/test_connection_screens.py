"""Tests for the Live views of a connection pin."""

from dataclasses import replace
from datetime import time
from typing import Final

from paperroute.core.alert_model import AlertsFetched, AlertsSnapshot
from paperroute.core.chooser import ChooserState
from paperroute.core.connection_model import Legs
from paperroute.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute.core.connection_view import ConnectionView
from paperroute.core.map_view import STRIP_MAP_WIDTH, MapView
from paperroute.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    Knob,
    KnobPressed,
    KnobTurned,
    LoadDepartures,
    ScreenView,
    Tick,
)
from paperroute.core.model import AutoWindow, Departure, Pin, RouteKind
from paperroute.core.places import shown
from paperroute.core.saved import (
    ConnectionMap,
    LiveView,
    Saved,
    Settings,
    seeded,
)
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.state_views import current_view, live_stop_view
from tests.core.alert_factories import alert
from tests.core.connection_factories import HOLLIS, LEG1, LEG2, WORK, at
from tests.core.factories import place
from tests.core.steps import wakeful
from tests.fakes import device_facts

PLAIN: Final[Pin] = Pin(
    origin=place('200060', name='Central Station'), kind=RouteKind.TRAIN,
)
PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)


def started(
    *,
    view: LiveView = LiveView.STOP,
    mode: ConnectionMap = ConnectionMap.CHANGE,
    pins: tuple[Pin, ...] = (WORK,),
) -> State:
    """
    Start at 7:30 on a Live view.

    Parameters
    ----------
    view : LiveView
        The Live view shown.
    mode : ConnectionMap
        How the Map shows a connection.
    pins : tuple[Pin, ...]
        The pins; the first is shown.

    Returns
    -------
    State
        State before any departures load.
    """
    saved = Saved(
        pins=pins,
        current=0,
        unpinned=None,
        view=view,
        zoom=seeded(pin=WORK).zoom,
        settings=Settings(connection_map=mode),
    )
    return wakeful(state=initial_state(
        saved=saved, now=at(7, 30), stored=True,
    ))


def legs_loaded(
    *,
    state: State,
    first: tuple[Departure, ...] = LEG1,
    second: tuple[Departure, ...] = LEG2,
) -> tuple[State, tuple[Effect, ...]]:
    """
    Load the Work pin's legs.

    Parameters
    ----------
    state : State
        State showing the Work pin.
    first : tuple[Departure, ...]
        Leg-1 departures.
    second : tuple[Departure, ...]
        Leg-2 departures.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after the load, and its effects.
    """
    return step(state, DeparturesLoaded(
        pin=WORK, departures=first, until=at(10, 30), legs=Legs(second=second),
    ))


def with_legs(*, state: State) -> State:
    """
    Load the Work pin's legs.

    Parameters
    ----------
    state : State
        State showing the Work pin.

    Returns
    -------
    State
        State after the load.
    """
    state, _ = legs_loaded(state=state)
    return state


def shown_view(*, state: State) -> ScreenView:
    """
    Build the screen the state shows.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    ScreenView
        The view.
    """
    return current_view(state=state, stop_view=live_stop_view(state=state))


def stepped(*, state: State, event: Event) -> State:
    """
    Apply one event.

    Parameters
    ----------
    state : State
        State.
    event : Event
        Event.

    Returns
    -------
    State
        State after.
    """
    state, _ = step(state, event)
    return state


def test_stop_view_is_connection_view() -> None:
    """The Stop view of a connection pin is the pair view."""
    view = shown_view(state=with_legs(state=started()))
    assert isinstance(view, ConnectionView)
    assert view.hero is not None
    assert view.hero.leaves_at == '7:33'


def test_cycle_keeps_strip_map() -> None:
    """Presses keep the four views, then Alerts while one is listed."""
    state = with_legs(state=started())
    views = []
    for _ in range(4):
        state = stepped(state=state, event=PRESS)
        views.append(state.view)
    assert views == [
        LiveView.MAP, LiveView.STRIP, LiveView.STRIP_MAP, LiveView.STOP,
    ]
    change = alert(
        alert_id='change', header='x', stops=frozenset({'2145648'}),
    )
    feed = 'v2/gtfs/alerts/buses'
    state = stepped(state=state, event=AlertsFetched(
        feed=feed,
        snapshot=AlertsSnapshot(
            feed=feed, fetched_at=at(7, 30), alerts=(change,),
        ),
    ))
    for _ in range(4):
        state = stepped(state=state, event=PRESS)
    assert state.view is LiveView.ALERTS


def test_strip_map_shows_connection_pane() -> None:
    """Strip + map has the connection map and the pair's sections."""
    for mode in ConnectionMap:
        state = started(
            view=LiveView.STRIP_MAP, mode=mode, pins=(PLAIN, WORK),
        )
        state = stepped(state=state, event=KnobTurned(
            knob=Knob.TOP, steps=1,
        ))
        view = shown_view(state=with_legs(state=state))
        assert isinstance(view, ConnectionStripMapView)
        assert view.map.base.trip is not None
        assert view.map.base.width == STRIP_MAP_WIDTH
        assert len(view.pane.sections) == 2
        centre = view.map.base.centre
        assert centre is not None
        assert centre[0] == (400.0 if mode is ConnectionMap.WHOLE else 72.0)
    state = with_legs(state=started(view=LiveView.STRIP_MAP))
    before = shown_view(state=state)
    state = stepped(state=state, event=KnobTurned(knob=Knob.BOTTOM, steps=1))
    after = shown_view(state=state)
    assert isinstance(before, ConnectionStripMapView)
    assert isinstance(after, ConnectionStripMapView)
    assert after.map.base.mpp < before.map.base.mpp


def test_strip_turn_selects_pair() -> None:
    """The bottom turn on the Strip picks the pair shown."""
    state = with_legs(state=started(view=LiveView.STRIP))
    state = stepped(state=state, event=KnobTurned(knob=Knob.BOTTOM, steps=2))
    assert state.scroll == 2
    view = shown_view(state=state)
    assert isinstance(view, ConnectionStripView)
    assert view.selected == 2
    state = stepped(state=state, event=KnobTurned(knob=Knob.BOTTOM, steps=-5))
    assert state.scroll == 0


def test_map_mode_whole() -> None:
    """The whole-trip Map centres between both legs' ends."""
    state = with_legs(state=started(
        view=LiveView.MAP, mode=ConnectionMap.WHOLE,
    ))
    view = shown_view(state=state)
    assert isinstance(view, MapView)
    assert view.base.trip is not None
    centre = view.base.centre
    assert centre is not None
    assert centre[0] == 400.0


def test_scroll_capped_at_last_pair() -> None:
    """Turning past the last pair stops there; one turn back leaves it."""
    state = with_legs(state=started(view=LiveView.STRIP))
    state = stepped(state=state, event=KnobTurned(knob=Knob.BOTTOM, steps=9))
    assert state.scroll == 4
    state = stepped(state=state, event=KnobTurned(knob=Knob.BOTTOM, steps=-1))
    view = shown_view(state=state)
    assert isinstance(view, ConnectionStripView)
    assert (state.scroll, view.selected) == (3, 3)
    state = stepped(state=state, event=PRESS)
    state = stepped(state=state, event=PRESS)
    assert state.view is LiveView.STOP
    state = stepped(state=state, event=KnobTurned(knob=Knob.BOTTOM, steps=9))
    assert state.scroll == 3


def test_top_press_opens_chooser_at_leg_one() -> None:
    """The chooser opens at leg 1; turning back shows the connection."""
    state = with_legs(state=started(pins=(WORK, PLAIN)))
    state = stepped(state=state, event=KnobPressed(knob=Knob.TOP))
    assert state.chooser == ChooserState(waiting=(), prefer=RouteKind.BUS)
    assert shown(state=state).origin == HOLLIS
    state = stepped(state=state, event=KnobPressed(knob=Knob.TOP))
    assert state.chooser is None
    state = stepped(state=state, event=KnobTurned(knob=Knob.TOP, steps=1))
    assert shown(state=state) == PLAIN
    state, effects = step(state, KnobTurned(knob=Knob.TOP, steps=-1))
    assert shown(state=state).ways == WORK.ways
    assert [
        effect.pin for effect in effects if isinstance(effect, LoadDepartures)
    ] == [WORK]
    assert isinstance(
        shown_view(state=with_legs(state=state)), ConnectionView,
    )


def test_auto_window_shows_connection() -> None:
    """A weekday window shows the connection at its start, view kept."""
    window = AutoWindow(
        days=frozenset(range(5)), start=time(7, 30), end=time(9),
    )
    work = replace(WORK, windows=(window,))
    saved = replace(
        seeded(pin=PLAIN), pins=(PLAIN, work), view=LiveView.MAP,
    )
    state = initial_state(saved=saved, now=at(7, 29), stored=True)
    state = replace(state, panel=replace(state.panel, facts=device_facts()))
    state = stepped(state=wakeful(state=state), event=Tick(now=at(7, 29)))
    state = stepped(state=state, event=Tick(now=at(7, 30)))
    assert shown(state=state) == work
    assert state.view is LiveView.MAP


def test_pin_flip_reloads_legs() -> None:
    """Flipping to the connection loads it and drops the old pin's load."""
    state = started(pins=(PLAIN, WORK))
    state, effects = step(state, KnobTurned(knob=Knob.TOP, steps=1))
    assert [
        effect.pin for effect in effects if isinstance(effect, LoadDepartures)
    ] == [WORK]
    after = stepped(state=state, event=DeparturesLoaded(
        pin=PLAIN, departures=LEG1, until=at(10, 30),
    ))
    assert after.places == state.places
    assert after.board == state.board
