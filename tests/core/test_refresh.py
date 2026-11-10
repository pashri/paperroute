"""Tests for when the panel gets a full or clear refresh."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute_device.core.kit_effects import Render
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.panel_views import MenuView
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobsHeld,
    KnobTurned,
)
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import START
from paperroute_testkit.core.steps import step_answered
from paperroute_transit.core.chooser import ChooserState
from paperroute_transit.core.map_view import MapView
from paperroute_transit.core.messages import Effect
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.refresh import quick_now
from paperroute_transit.core.saved import LiveView, seeded
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import State
from paperroute_transit.core.stop_view import StopView
from tests.core.test_state import PIN, PRESS, effects_of, fresh, loaded

IGNORED = KnobHeld(knob=Knob.TOP)
TURN = KnobTurned(knob=Knob.BOTTOM, steps=1)


def drawn(*, touched_at: datetime | None = None) -> State:
    """
    Make a state with a view drawn.

    Parameters
    ----------
    touched_at : datetime | None
        The last knob touch.

    Returns
    -------
    State
        The state at `START`.
    """
    state = step(loaded(state=fresh()), Tick(now=START))[0]
    return replace(
        state, refresh=replace(state.refresh, touched_at=touched_at),
    )


def on_map() -> State:
    """
    Make a state with the Map view drawn.

    Returns
    -------
    State
        The state at `START`.
    """
    state = replace(drawn(), view=LiveView.MAP)
    return step_answered(state=state, event=Tick(now=START))[0]


def renders(*, effects: tuple[Effect, ...]) -> list[Render]:
    """
    Pick the renders.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[Render]
        The Render effects.
    """
    return effects_of(effects=effects, kind=Render)


def fulls(*, effects: tuple[Effect, ...]) -> list[Render]:
    """
    Pick the full renders.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[Render]
        The full Render effects.
    """
    return [
        render
        for render in effects_of(effects=effects, kind=Render)
        if render.full
    ]


def ticked(*, state: State, after: timedelta) -> tuple[State, list[Render]]:
    """
    Tick a span after `START`.

    Parameters
    ----------
    state : State
        State.
    after : timedelta
        The span.

    Returns
    -------
    tuple[State, list[Render]]
        State and its full renders.
    """
    state, effects = step(state, Tick(now=START + after))
    return state, fulls(effects=effects)


def test_loaded_start_draws_at_once() -> None:
    """With the timetable loaded, the first frame is drawn full at once."""
    state = fresh()
    board = replace(state.board, loaded_until=START + timedelta(hours=1))
    _, effects = step(replace(state, board=board), Tick(now=START))
    [render] = renders(effects=effects)
    assert render.full


def test_loading_screen_waits_five_seconds() -> None:
    """Still loading, the first frame waits 5 s, then is drawn full."""
    state = initial_state(
        saved=seeded(pin=PIN, city=SYDNEY),
        now=START,
        stored=True,
        city=SYDNEY,
    )
    state, effects = step(state, Tick(now=START))
    assert not renders(effects=effects)
    assert state.refresh.started_at == START
    state, effects = step(state, Tick(now=START + timedelta(seconds=4)))
    assert not renders(effects=effects)
    _, effects = step(state, Tick(now=START + timedelta(seconds=5)))
    [render] = renders(effects=effects)
    assert render.full


def test_loading_hold_ends_when_clock_steps_back() -> None:
    """A clock stepped back past the start stops holding the screen."""
    state = initial_state(
        saved=seeded(pin=PIN, city=SYDNEY),
        now=START,
        stored=True,
        city=SYDNEY,
    )
    state, effects = step(state, Tick(now=START))
    assert not renders(effects=effects)
    _, effects = step(state, Tick(now=START - timedelta(hours=1)))
    [render] = renders(effects=effects)
    assert render.full


def test_no_scheduled_full_refresh() -> None:
    """The Map view left idle for 30 minutes gets no full refresh."""
    state = on_map()
    assert isinstance(state.last_view, MapView)
    _, found = ticked(state=state, after=timedelta(minutes=30))
    assert not found


def test_entering_menu_from_map_is_clear() -> None:
    """Opening a menu over the map is a clear refresh, not a full one."""
    state, effects = step(on_map(), KnobsHeld())
    [render] = renders(effects=effects)
    assert isinstance(render.view, MenuView)
    assert (render.full, render.clear) == (False, True)
    assert state.refresh.in_menu


def test_turning_inside_menu_is_not_full() -> None:
    """A change inside a menu is neither full nor clear."""
    state, _ = step(on_map(), KnobsHeld())
    _, effects = step(state, TURN)
    [render] = renders(effects=effects)
    assert (render.full, render.clear) == (False, False)


def test_leaving_menu_to_stop_view_is_full() -> None:
    """Closing a menu to the Stop view is a full refresh."""
    state, _ = step(drawn(), KnobsHeld())
    closed = replace(state, panel=replace(state.panel, menu=None))
    state, effects = step(closed, Tick(now=START))
    [render] = renders(effects=effects)
    assert isinstance(render.view, StopView)
    assert (render.full, render.clear) == (True, False)
    assert not state.refresh.in_menu


def test_opening_chooser_is_clear() -> None:
    """The route chooser counts as a menu, whatever view it shows."""
    state = replace(on_map(), chooser=ChooserState())
    state, effects = step(state, Tick(now=START))
    [render] = renders(effects=effects)
    assert (render.full, render.clear) == (False, True)
    assert state.refresh.in_menu


def test_map_change_is_full() -> None:
    """Into or out of a map view is full; between map views is partial."""
    state, effects = step_answered(state=drawn(), event=PRESS)
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full
    state, effects = step_answered(
        state=replace(state, view=LiveView.STRIP_MAP), event=Tick(now=START),
    )
    [render] = effects_of(effects=effects, kind=Render)
    assert not render.full
    _, effects = step_answered(
        state=replace(state, view=LiveView.STRIP), event=Tick(now=START),
    )
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full


def test_touch_is_noted() -> None:
    """A knob event notes its time, even one that is ignored."""
    state = drawn()
    for event in (PRESS, IGNORED):
        after, _ = step(state, event)
        assert after.refresh.touched_at == START


def test_waking_is_full() -> None:
    """The first frame after the sleep screen is a full refresh."""
    state = drawn()
    asleep, _ = ticked(
        state=replace(state, panel=replace(state.panel, asleep=True)),
        after=timedelta(),
    )
    _, effects = step(asleep, PRESS)
    [render] = effects_of(effects=effects, kind=Render)
    assert render.full
    assert not isinstance(render.view, SleepView)


def test_quick_now_lasts_three_seconds() -> None:
    """A knob touched 2.9 s ago is quick; at 3 s or never it is not."""
    base = drawn()
    for age, quick in ((2.9, True), (3.0, False)):
        touched = START - timedelta(seconds=age)
        refresh = replace(base.refresh, touched_at=touched)
        state = replace(base, now=START, refresh=refresh)
        assert quick_now(state=state) is quick
    assert quick_now(state=base) is False
