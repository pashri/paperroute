"""Tests for state: pins."""

from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest

from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobTurned,
)
from paperroute_sydney.city import SYDNEY, SydneyFeed
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import step_untouched
from paperroute_transit.core.map_state import area_box
from paperroute_transit.core.map_view import DEFAULT_ZOOM, MapView
from paperroute_transit.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    LoadDepartures,
    LoadModes,
    MapData,
    MapDataFailed,
    ModesLoaded,
    ScreenView,
)
from paperroute_transit.core.places import shown
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LIVE_VIEWS, LiveView, default_saved
from paperroute_transit.core.state_model import State
from paperroute_transit.core.stop_view import HORIZON, StopView
from paperroute_transit.core.strip_model import StripMapView, StripView
from paperroute_transit.core.tiles import ZOOMS
from tests.core.state.helpers import (
    CENTRE,
    ELSEWHERE,
    HOLD,
    PINS,
    PRESS,
    effects_of,
    fresh,
    loaded,
    map_zoom,
    pinned,
    top,
)


def tag_text(*, state: State) -> str:
    """
    Read the pin tag of the view last drawn.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str
        The tag's text.
    """
    assert isinstance(state.last_view, (StopView, MapView))
    return state.last_view.tag.text


def test_top_turn_flips_pins_and_wraps() -> None:
    """The top knob steps through the pins and wraps at both ends."""
    state, _ = step(pinned(), top(steps=1))
    assert (shown(state=state), tag_text(state=state)) == (PINS[1], 'pin 2/3')
    state, _ = step(state, top(steps=2))
    assert (shown(state=state), tag_text(state=state)) == (PINS[0], 'pin 1/3')
    state, _ = step(state, top(steps=-1))
    assert (shown(state=state), tag_text(state=state)) == (PINS[2], 'pin 3/3')


def test_top_turn_drops_unpinned_and_keeps_current() -> None:
    """The first turn away from the unpinned place shows the current pin."""
    state, _ = step(pinned(current=1, unpinned=ELSEWHERE), top(steps=5))
    assert shown(state=state) == PINS[1]
    assert state.places.unpinned is None
    assert state.places.current == 1


def test_top_turn_without_pins_does_nothing() -> None:
    """With only the unpinned place there is nothing to turn to."""
    state, _ = step(
        initial_state(
            saved=default_saved(city=SYDNEY),
            now=START,
            city=SYDNEY,
        ),
        Tick(now=START),
    )
    assert step_untouched(state=state, event=top(steps=1)) == (state, ())


def test_hold_pins_unpinned_at_the_end() -> None:
    """Holding pins the unpinned place last and selects it, in place."""
    state, effects = step(pinned(unpinned=ELSEWHERE), HOLD)
    assert state.places.pins == (*PINS, ELSEWHERE)
    assert (state.places.current, state.places.unpinned) == (3, None)
    assert tag_text(state=state) == 'pin 4/4'
    assert not effects_of(effects=effects, kind=LoadDepartures)


def test_hold_unpins_current_into_unpinned() -> None:
    """Holding unpins the current pin and keeps showing its departures."""
    state = loaded(state=pinned(current=1))
    state, effects = step(state, HOLD)
    assert state.places.pins == (PINS[0], PINS[2])
    assert state.places.unpinned == PINS[1]
    assert state.places.current == 1
    assert tag_text(state=state) == 'not pinned'
    assert state.board.departures
    assert not effects_of(effects=effects, kind=LoadDepartures)


def test_hold_with_duplicate_selects_existing_pin() -> None:
    """Pinning a place already pinned selects that pin, in place."""
    renamed = replace(PINS[2].origin, name='Renamed')
    duplicate = replace(PINS[2], origin=renamed)
    before = loaded(state=pinned(unpinned=duplicate))
    state, effects = step(before, HOLD)
    assert state.places.pins == PINS
    assert (state.places.current, state.places.unpinned) == (2, None)
    assert state.board == before.board
    assert not effects_of(effects=effects, kind=LoadDepartures)


@pytest.mark.parametrize('view', [LiveView.STRIP, LiveView.STRIP_MAP])
def test_flip_and_hold_on_the_strip_views(view: LiveView) -> None:
    """The top turn and the bottom hold work on both strip views."""
    state, effects = step(pinned(view=view), top(steps=1))
    assert (state.view, shown(state=state)) == (view, PINS[1])
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == PINS[1]
    state, _ = step(state, HOLD)
    assert state.places.pins == (PINS[0], PINS[2])
    assert (state.view, state.places.unpinned) == (view, PINS[1])


@pytest.mark.parametrize('view', [LiveView.STRIP, LiveView.STRIP_MAP])
def test_top_turn_while_held_does_nothing(view: LiveView) -> None:
    """A top turn while the top knob is held is left unassigned."""
    state = pinned(view=view)
    held = KnobHeldTurned(knob=Knob.TOP, steps=1)
    assert step_untouched(state=state, event=held) == (state, ())


@pytest.mark.parametrize('view', list(LiveView))
def test_top_hold_does_nothing(view: LiveView) -> None:
    """Holding the top knob alone does nothing, on every Live view."""
    state = pinned(unpinned=ELSEWHERE, view=view)
    assert step_untouched(
        state=state, event=KnobHeld(knob=Knob.TOP),
    ) == (state, ())


def test_bottom_press_cycles_four_views() -> None:
    """Presses go Stop, Map, Strip, Strip + map and back to Stop."""
    state, _ = step(fresh(centre=CENTRE), Tick(now=START))
    views: list[LiveView] = []
    drawn: list[type[ScreenView]] = []
    for _ in LIVE_VIEWS:
        state, effects = step(state, PRESS)
        views.append(state.view)
        drawn.extend(
            type(cast(ScreenView, effect.view)) for effect in effects_of(
                effects=effects, kind=Render
            )
        )
    assert views == [
        LiveView.MAP,
        LiveView.STRIP,
        LiveView.STRIP_MAP,
        LiveView.STOP,
    ]
    assert drawn == [MapView, StripView, StripMapView, StopView]


def test_bottom_turn_scrolls_stop_and_zooms_map_and_strip_map() -> None:
    """The Stop view scrolls; both map views zoom; the Strip moves neither."""
    state = loaded(state=fresh(centre=CENTRE), count=20)
    state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=2))
    assert (state.scroll, state.map.step) == (2, 0)
    zooms = []
    for _ in range(3):
        state, _ = step(state, PRESS)
        state, _ = step(state, KnobTurned(knob=Knob.BOTTOM, steps=-1))
        zooms.append(map_zoom(state=state))
    assert zooms == [DEFAULT_ZOOM + 1, DEFAULT_ZOOM + 1, DEFAULT_ZOOM + 2]
    assert state.scroll == 2


def test_flip_reloads_departures_and_recentres_map() -> None:
    """A new pin empties the board, loads its departures and moves the map."""
    state = loaded(state=pinned(view=LiveView.MAP))
    state, effects = step(state, top(steps=1))
    [load] = effects_of(effects=effects, kind=LoadDepartures)
    assert load.pin == PINS[1]
    assert state.board.departures == ()
    assert state.board.loading
    assert state.map.centre == PINS[1].origin.point


def test_departures_for_another_place_are_dropped() -> None:
    """A load that lands after a flip doesn't fill the new place's board."""
    state, _ = step(pinned(), top(steps=1))
    late = departure(departs=START + timedelta(minutes=3))
    event = DeparturesLoaded(
        pin=PINS[0], departures=(late,), until=START + HORIZON
    )
    state, _ = step(state, event)
    assert state.board.departures == ()
    assert state.board.loading


def test_stale_departures_failure_keeps_the_new_load() -> None:
    """A failure for the old place doesn't clear the new place's load."""
    state, _ = step(pinned(), top(steps=1))
    state, effects = step(state, DeparturesFailed(pin=PINS[0]))
    assert state.board.loading
    assert effects == ()


def test_modes_for_the_old_area_are_dropped_after_a_flip() -> None:
    """Modes or a modes failure for the old area land nowhere."""
    mpp = ZOOMS[DEFAULT_ZOOM]
    old = area_box(centre=PINS[0].origin.point or CENTRE, mpp=mpp)
    state, effects = step(pinned(view=LiveView.MAP), top(steps=1))
    [load] = effects_of(effects=effects, kind=LoadModes)
    assert load.box == area_box(centre=PINS[1].origin.point or CENTRE, mpp=mpp)
    event = ModesLoaded(mpp=mpp, box=old, modes=frozenset({SydneyFeed.FERRY}))
    state, _ = step(state, event)
    assert not state.map.area_modes
    request = LoadModes(mpp=mpp, box=old)
    state, _ = step(state, MapDataFailed(request=request))
    assert not state.map.failed_at
    assert state.map.pending_modes == {mpp}


def test_flip_forgets_the_old_places_map_failures() -> None:
    """A failure at the old place doesn't hold back the new place's map."""
    mpp = ZOOMS[DEFAULT_ZOOM]
    box = area_box(centre=PINS[0].origin.point or CENTRE, mpp=mpp)
    state = pinned(view=LiveView.MAP)
    request = LoadModes(mpp=mpp, box=box)
    state, _ = step(state, MapDataFailed(request=request))
    assert MapData.MODES in state.map.failed_at
    state, effects = step(state, top(steps=1))
    assert not state.map.failed_at
    assert effects_of(effects=effects, kind=LoadModes)
