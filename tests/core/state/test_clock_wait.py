"""Tests for state: clock wait."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Final

from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import MenuRow, PowerAction
from paperroute_device.core.device_settings import Window
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import ProbeDevice, Render, SaveState
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import SAVE_WAIT
from paperroute_device.core.panel_views import NoticeView
from paperroute_hardware.core.knob_events import Knob, KnobsHeld, KnobTurned
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.kit_steps import (
    QUIET_PARTS,
    device_of,
    kit_of,
    kit_step,
    settings_posted,
    step,
)
from paperroute_testkit.core.steps import step_answered
from paperroute_transit.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    FetchFeed,
    LoadDepartures,
)
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.places import shown
from paperroute_transit.core.state_model import (
    CLOCK_WAIT,
    LOOKBACK,
    RELOAD_BEFORE,
    State,
)
from paperroute_transit.core.state_model import clock_waiting as clock_held
from paperroute_transit.core.stop_view import HORIZON, StopView
from paperroute_transit.transit_app import TransitApp
from tests.core.state.helpers import PIN, PRESS, fresh, loaded, saves

UNSYNCED_AT: Final[datetime] = datetime(
    2026, 11, 5, 23, 10, tzinfo=SYDNEY_ZONE,
)


SYNCED_AT: Final[datetime] = datetime(2026, 11, 6, 7, 42, tzinfo=SYDNEY_ZONE)


def clock_waiting(
    *, state: State | None = None
) -> tuple[State, tuple[Effect, ...]]:
    """
    Start on an unsynced clock and load departures.

    Parameters
    ----------
    state : State | None
        State before the first tick; a fresh one by default.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with departures loaded, and every effect so far.
    """
    state, effects = step(state or fresh(), Tick(now=START, synced=False))
    deps = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    event = DeparturesLoaded(
        pin=shown(state=state),
        departures=deps,
        until=START + HORIZON + RELOAD_BEFORE,
    )
    state, more = step(state, event)
    return state, (*effects, *more)


def drawn_or_fetched(*, effects: tuple[Effect, ...]) -> list[Effect]:
    """
    Pick out the renders and feed fetches.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[Effect]
        The Render and FetchFeed effects, in order.
    """
    return [
        effect
        for effect in effects
        if isinstance(effect, Render | FetchFeed)
    ]


def test_unsynced_clock_draws_nothing() -> None:
    """For 90 s of unsynced ticks nothing is drawn or fetched."""
    state, effects = clock_waiting()
    for seconds in range(5, 95, 5):
        state, more = step(
            state, Tick(now=START + timedelta(seconds=seconds), synced=False),
        )
        effects = (*effects, *more)
    assert drawn_or_fetched(effects=effects) == []
    assert state.last_view is None


def test_clock_wait_gives_up_after_two_minutes() -> None:
    """With no sync, the first frame comes once `CLOCK_WAIT` has passed."""
    state, effects = clock_waiting()
    late = START + CLOCK_WAIT - timedelta(seconds=5)
    state, more = step(state, Tick(now=late, synced=False))
    assert drawn_or_fetched(effects=(*effects, *more)) == []
    state, more = step_answered(
        state=state, event=Tick(now=START + CLOCK_WAIT, synced=False),
    )
    assert any(isinstance(effect, FetchFeed) for effect in more)
    assert sum(isinstance(effect, Render) for effect in more) == 1


def test_sync_jump_starts_afresh() -> None:
    """A synced tick hours later reloads, fires a cycle and draws once."""
    state, _ = step(
        fresh(now=UNSYNCED_AT),
        Tick(now=UNSYNCED_AT, synced=False),
    )
    state, early = step(
        state, Tick(now=UNSYNCED_AT + timedelta(seconds=5), synced=False),
    )
    state, effects = step(state, Tick(now=SYNCED_AT))
    assert state.clock_wait_until is None
    assert state.refresh.started_at == SYNCED_AT
    assert LoadDepartures(
        pin=PIN,
        start=SYNCED_AT - LOOKBACK,
        end=SYNCED_AT + HORIZON + RELOAD_BEFORE,
    ) in effects
    assert not any(isinstance(effect, ProbeDevice) for effect in early)
    assert any(isinstance(effect, ProbeDevice) for effect in effects)
    deps = tuple(
        departure(departs=SYNCED_AT + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    state, loads = step_answered(
        state=state,
        event=DeparturesLoaded(
            pin=PIN,
            departures=deps,
            until=SYNCED_AT + HORIZON + RELOAD_BEFORE,
        ),
    )
    assert any(isinstance(effect, FetchFeed) for effect in loads)
    renders = [e for e in (*effects, *loads) if isinstance(e, Render)]
    assert len(renders) == 1
    assert state.last_view is not None


def test_sync_jump_fires_a_cycle_on_the_tick() -> None:
    """Feeds needed during the wait are fetched on the synced tick."""
    state, _ = clock_waiting()
    later = START + timedelta(hours=3, seconds=7)
    state, effects = step(state, Tick(now=later))
    assert state.cycle.started == later
    assert any(isinstance(effect, FetchFeed) for effect in effects)
    assert not any(isinstance(effect, Render) for effect in effects)


def test_sync_clears_the_wrong_date_board() -> None:
    """Departures loaded before the clock was set are dropped at once."""
    state, _ = clock_waiting()
    assert state.board.departures
    later = START + timedelta(hours=3, seconds=7)
    state, effects = step(state, Tick(now=later))
    assert state.board.departures == ()
    assert state.board.loading
    assert not any(isinstance(effect, Render) for effect in effects)


def test_load_ending_before_now_is_dropped() -> None:
    """A load whose window has ended when it lands is not shown."""
    state, _ = clock_waiting()
    later = START + timedelta(hours=5, seconds=7)
    state, _ = step(state, Tick(now=later))
    old = DeparturesLoaded(
        pin=shown(state=state),
        departures=(departure(departs=START + timedelta(minutes=3)),),
        until=START + HORIZON + RELOAD_BEFORE,
    )
    state, effects = step(state, old)
    assert state.board.departures == ()
    assert state.board.loaded_until is None
    assert not any(isinstance(effect, Render) for effect in effects)
    state, more = step(state, Tick(now=later + timedelta(seconds=5)))
    assert any(isinstance(effect, LoadDepartures) for effect in more)


def test_clock_wait_expiry_makes_probe_due() -> None:
    """The device is probed on the tick the clock wait gives up."""
    state, _ = clock_waiting()
    state, _ = step(
        state, Tick(now=START + timedelta(seconds=5), synced=False),
    )
    late = START + CLOCK_WAIT - timedelta(seconds=5)
    state, _ = step(state, Tick(now=late, synced=False))
    state = replace(
        state,
        panel=replace(state.panel, probed_at=late, probing=False),
    )
    state, effects = step(
        state, Tick(now=START + CLOCK_WAIT, synced=False),
    )
    assert any(isinstance(effect, ProbeDevice) for effect in effects)
    state, effects = step(
        state,
        Tick(now=START + CLOCK_WAIT + timedelta(seconds=5), synced=False),
    )
    assert not any(isinstance(effect, ProbeDevice) for effect in effects)


def test_clock_wait_holds_only_once() -> None:
    """A clock stepping back after the give-up does not hold again."""
    state, _ = clock_waiting()
    state, _ = step(state, Tick(now=START + CLOCK_WAIT, synced=False))
    back = START + timedelta(seconds=30)
    state, _ = step(state, Tick(now=back, synced=False))
    assert not clock_held(state=state)
    state, _ = step(state, Tick(now=back + timedelta(hours=3)))
    assert state.refresh.started_at == back + timedelta(hours=3)
    assert state.clock_wait_until is None


def test_synced_start_never_waits() -> None:
    """A synced first tick sets no clock wait."""
    state, _ = step(fresh(), Tick(now=START))
    assert state.clock_wait_until is None


def test_events_before_the_first_tick_wait_for_it() -> None:
    """Nothing is drawn or fetched until a tick reports the clock."""
    state = replace(fresh(), clock_known=False)
    deps = tuple(
        departure(departs=START + timedelta(minutes=3 * (i + 1)))
        for i in range(3)
    )
    state, effects = step(
        state,
        DeparturesLoaded(
            pin=shown(state=state),
            departures=deps,
            until=START + HORIZON + RELOAD_BEFORE,
        ),
    )
    assert drawn_or_fetched(effects=effects) == []
    assert state.last_view is None
    state, effects = step_answered(
        state=state, event=Tick(now=START + timedelta(seconds=5)),
    )
    assert state.clock_known
    assert any(isinstance(effect, FetchFeed) for effect in effects)
    assert sum(isinstance(effect, Render) for effect in effects) == 1


def test_unsynced_first_tick_starts_the_wait() -> None:
    """An unsynced first tick after start-up waits as usual."""
    state = replace(fresh(), clock_known=False)
    state, effects = clock_waiting(state=state)
    assert state.clock_known
    assert state.clock_wait_until == START + CLOCK_WAIT
    assert drawn_or_fetched(effects=effects) == []


def test_unsynced_again_after_drawing_does_not_wait() -> None:
    """Once a frame is drawn, an unsynced tick starts no new wait."""
    state = loaded(state=fresh())
    assert state.last_view is not None
    state, _ = step(
        state, Tick(now=START + timedelta(seconds=5), synced=False),
    )
    assert state.clock_wait_until is None


def test_knob_draws_during_clock_wait() -> None:
    """A person's turn draws at once while the clock is unsynced."""
    state, _ = clock_waiting()
    _, effects = step(state, KnobTurned(knob=Knob.BOTTOM, steps=1))
    assert sum(isinstance(effect, Render) for effect in effects) == 1


def test_menu_draws_during_clock_wait() -> None:
    """A menu opened by a person draws while the clock is unsynced."""
    state, _ = clock_waiting()
    _, effects = step(state, KnobsHeld())
    assert sum(isinstance(effect, Render) for effect in effects) == 1


def test_notice_draws_during_clock_wait() -> None:
    """Shutting down is drawn while the clock is unsynced."""
    state, _ = clock_waiting()
    _, effects = step(state, PowerRequested(action=PowerAction.OFF))
    renders = [e for e in effects if isinstance(e, Render)]
    assert [type(render.view) for render in renders] == [NoticeView]


def test_sleep_screen_held_during_clock_wait() -> None:
    """In quiet hours the sleep screen waits for the clock too."""
    quiet = Window(start=time(7), end=time(9))
    state = fresh()
    device = replace(state.settings.device, my_hours=quiet)
    state = replace(state, settings=replace(state.settings, device=device))
    state, effects = clock_waiting(state=state)
    state, more = step(
        state, Tick(now=START + timedelta(seconds=5), synced=False),
    )
    assert state.panel.asleep
    assert drawn_or_fetched(effects=(*effects, *more)) == []
    _, more = step(state, Tick(now=START + CLOCK_WAIT, synced=False))
    renders = [e for e in more if isinstance(e, Render)]
    assert [type(render.view) for render in renders] == [SleepView]


def test_kit_steps_keep_a_pending_save_across_calls() -> None:
    """A settings change made in one call is saved by a later call's tick."""
    kit, _ = kit_step(kit=kit_of(state=fresh()), event=Tick(now=START))
    kit, _ = kit_step(kit=kit, event=settings_posted(larger_text=True))
    assert kit.changed_at == START
    kit, effects = kit_step(
        kit=kit, event=Tick(now=START + timedelta(seconds=1)),
    )
    assert not [e for e in effects if isinstance(e, SaveState)]
    _, effects = kit_step(kit=kit, event=Tick(now=START + SAVE_WAIT))
    [save] = [e for e in effects if isinstance(e, SaveState)]
    assert isinstance(save.saved, KitSaved)
    assert save.saved.device.larger_text


def test_chained_steps_carry_a_pending_save() -> None:
    """A change in one step is saved by a chained step's later tick."""
    state, _ = step(fresh(), Tick(now=START))
    state, _ = step(state, settings_posted(larger_text=True))
    state, effects = step(state, Tick(now=START + timedelta(seconds=1)))
    assert not saves(effects=effects)
    _, effects = step(state, Tick(now=START + SAVE_WAIT))
    assert len(saves(effects=effects)) == 1


def test_a_replaced_state_starts_with_nothing_to_save() -> None:
    """A state edited since its step starts as if just written."""
    state, _ = step(fresh(), Tick(now=START))
    state, _ = step(state, settings_posted(larger_text=True))
    _, effects = step(replace(state), Tick(now=START + SAVE_WAIT))
    assert not saves(effects=effects)


def test_kit_steps_keep_the_sleep_frame_flag() -> None:
    """A wake after a drawn sleep frame redraws in full."""
    kit, _ = kit_step(
        kit=kit_of(state=loaded(state=fresh())), event=Tick(now=START),
    )
    index = tuple(MenuRow).index(MenuRow.SLEEP)
    events: tuple[Event, ...] = (
        KnobsHeld(),
        KnobTurned(knob=Knob.BOTTOM, steps=index),
    )
    for event in events:
        kit, _ = kit_step(kit=kit, event=event)
    kit, _ = kit_step(kit=kit, event=PRESS)
    assert kit.device.panel.asleep
    assert kit.device.last_was_sleep
    kit, effects = kit_step(kit=kit, event=PRESS)
    [render] = [e for e in effects if isinstance(e, Render)]
    assert isinstance(render.view, StopView)
    assert render.full
    assert not kit.device.last_was_sleep


def test_knob_turn_draws_during_a_cycle_wait() -> None:
    """A turn while a cycle waits for its feeds is drawn at once."""
    state, _ = step(loaded(state=fresh()), PRESS)
    held_at = START + timedelta(seconds=5 * 60 + 2)
    state, effects = step(state, Tick(now=held_at))
    assert not [e for e in effects if isinstance(e, Render)]
    turn = KnobTurned(knob=Knob.BOTTOM, steps=1)
    app = TransitApp(city=SYDNEY, parts=QUIET_PARTS)
    begun_state = app.begin(
        state=state, device=device_of(state=state), event=turn,
    )
    assert begun_state.cycle.free
    state, effects = step(state, turn)
    assert len([e for e in effects if isinstance(e, Render)]) == 1
    assert not state.cycle.free
