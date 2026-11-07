"""Tests for the Settings menu and confirming on the device."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.chooser_view import ChooserView
from paperroute.core.device import device_status
from paperroute.core.device_model import (
    ConfirmAction,
    Menu,
    MenuRow,
    Origin,
    PanelScreen,
    ResetKind,
)
from paperroute.core.menu import CONFIRM_WAIT, MENU_IDLE
from paperroute.core.messages import (
    ConfirmAsked,
    ControlPower,
    DeviceProbed,
    Effect,
    Event,
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
    NoticeView,
    PowerAction,
    ProbeDevice,
    Render,
    ResetDevice,
    SaveState,
    Tick,
)
from paperroute.core.panel_views import ConfirmView, MenuView, PhoneView
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.stop_view import StopView
from paperroute_testkit.core.factories import START
from paperroute_testkit.fakes import device_facts
from tests.core.test_rebuild import pinned

BOTH: Final[KnobsHeld] = KnobsHeld()
DOWN: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
UP: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)


def turn(*, steps: int) -> KnobTurned:
    """
    Turn the bottom knob.

    Parameters
    ----------
    steps : int
        Detents.

    Returns
    -------
    KnobTurned
        The event.
    """
    return KnobTurned(knob=Knob.BOTTOM, steps=steps)


def run(
    *, state: State, events: tuple[Event, ...]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply events in turn.

    Parameters
    ----------
    state : State
        State before.
    events : tuple[Event, ...]
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The state after, and the last event's effects.
    """
    effects: tuple[Effect, ...] = ()
    for event in events:
        state, effects = step(state, event)
    return state, effects


def opened_at(*, row: MenuRow) -> State:
    """
    Open the menu with the cursor on a row.

    Parameters
    ----------
    row : MenuRow
        The row.

    Returns
    -------
    State
        The state with the menu open.
    """
    index = tuple(MenuRow).index(row)
    state, _ = run(state=pinned(), events=(BOTH, turn(steps=index)))
    return state


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
    return [effect.view for effect in effects if isinstance(effect, Render)]


def test_both_held_opens_menu() -> None:
    """Holding both opens the menu over the Stop view and the chooser."""
    state, effects = step(pinned(), BOTH)
    assert state.panel.menu == Menu()
    assert isinstance(renders(effects=effects)[0], MenuView)
    chosen, _ = step(pinned(), UP)
    assert chosen.chooser is not None
    state, _ = run(state=chosen, events=(BOTH, UP))
    assert state.panel.menu is None
    assert state.chooser is not None
    assert isinstance(state.last_view, ChooserView)


def test_cursor_moves_and_clamps() -> None:
    """The cursor stays on the rows."""
    state, effects = run(state=pinned(), events=(BOTH, turn(steps=-1)))
    assert state.panel.menu == Menu(cursor=0)
    assert not effects
    state, _ = step(state, turn(steps=20))
    assert state.panel.menu == Menu(cursor=7)


def test_menu_scrolls_to_reset() -> None:
    """The last row scrolls the list by one."""
    state = opened_at(row=MenuRow.RESET)
    assert isinstance(state.last_view, MenuView)
    assert state.last_view.first == 1
    assert state.last_view.cursor == 7


def test_top_press_closes_menu() -> None:
    """A top press closes the menu and draws the Live view."""
    state, effects = run(state=pinned(), events=(BOTH, UP))
    assert state.panel.menu is None
    assert isinstance(renders(effects=effects)[0], StopView)


def test_menu_ignores_holds_and_top_turn() -> None:
    """Holds, top turns and held turns do nothing in the menu."""
    state, _ = step(pinned(), BOTH)
    for event in (
        KnobHeld(knob=Knob.TOP),
        KnobHeld(knob=Knob.BOTTOM),
        KnobTurned(knob=Knob.TOP, steps=1),
        KnobHeldTurned(knob=Knob.BOTTOM, steps=1),
        BOTH,
    ):
        assert step(state, event) == (state, ())


def test_bottom_hold_still_pins_with_menu_closed() -> None:
    """A bottom hold acts on the Live view once the menu is closed."""
    before, _ = run(state=pinned(), events=(BOTH, UP))
    after, _ = step(before, KnobHeld(knob=Knob.BOTTOM))
    assert after.places != before.places


def answered(*, seconds: int) -> State:
    """
    Answer the first tick's probe, then open the menu on Device.

    Parameters
    ----------
    seconds : int
        Seconds after START the menu opens.

    Returns
    -------
    State
        The state with the cursor on Device.
    """
    state, _ = run(
        state=pinned(),
        events=(
            DeviceProbed(facts=device_facts()),
            Tick(now=START + timedelta(seconds=seconds)),
            BOTH,
            turn(steps=1),
        ),
    )
    return state


def test_device_row_opens_device_screen_and_probes() -> None:
    """The Device row opens its screen and asks for the facts."""
    state, effects = step(answered(seconds=5), DOWN)
    assert state.panel.menu == Menu(screen=PanelScreen.DEVICE, cursor=1)
    assert ProbeDevice() in effects
    state, _ = step(state, UP)
    assert state.panel.menu == Menu(cursor=1)


def probes(*, effects: tuple[Effect, ...]) -> int:
    """
    Count the probes asked for.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    int
        How many ProbeDevice effects there are.
    """
    return sum(isinstance(effect, ProbeDevice) for effect in effects)


def test_device_screen_probes_every_5s_one_at_a_time() -> None:
    """The Device screen probes every 5 s, never over a running probe."""
    state, effects = step(answered(seconds=5), DOWN)
    assert probes(effects=effects) == 1
    state, effects = step(state, Tick(now=START + timedelta(seconds=9)))
    assert probes(effects=effects) == 0
    state, effects = step(state, Tick(now=START + timedelta(seconds=10)))
    assert probes(effects=effects) == 0
    state, _ = step(state, DeviceProbed(facts=device_facts()))
    state, effects = step(state, Tick(now=START + timedelta(seconds=10)))
    assert probes(effects=effects) == 1


def test_menu_closes_after_two_idle_minutes() -> None:
    """The menu closes to Live after 2 minutes with no knob input."""
    state, _ = run(state=pinned(), events=(BOTH, turn(steps=2), DOWN))
    state, _ = step(state, Tick(now=START + MENU_IDLE - timedelta(seconds=1)))
    assert state.panel.menu is not None
    state, effects = step(state, Tick(now=START + MENU_IDLE))
    assert state.panel.menu is None
    assert isinstance(renders(effects=effects)[0], StopView)


@pytest.mark.parametrize(
    'action', [ConfirmAction.RESTART, ConfirmAction.SHUT_DOWN],
)
def test_phone_power_not_asked_on_panel(action: ConfirmAction) -> None:
    """Restart and Shut down from a phone never show a confirm."""
    state = pinned()
    assert step(state, ConfirmAsked(action=action)) == (state, ())


def test_phone_row_shows_addresses() -> None:
    """The phone screen opens the IP, and names paperroute.local."""
    facts = device_facts()
    wifi = replace(facts.wifi, managed=True, ssid='Home', ip='192.168.1.40')
    state = replace(
        opened_at(row=MenuRow.PHONE),
        panel=replace(
            opened_at(row=MenuRow.PHONE).panel,
            facts=replace(facts, wifi=wifi),
        ),
    )
    _, effects = step(state, DOWN)
    assert renders(effects=effects) == [
        PhoneView(
            clock='8:00',
            url='http://192.168.1.40/',
            addresses=('paperroute.local', 'http://192.168.1.40'),
            network='Home',
        ),
    ]


def test_restart_asks_then_bottom_press_restarts() -> None:
    """Restart asks first, then saves, draws the notice and restarts."""
    state, effects = step(opened_at(row=MenuRow.RESTART), DOWN)
    assert isinstance(renders(effects=effects)[0], ConfirmView)
    state, effects = step(state, DOWN)
    assert state.panel.menu is None
    assert state.panel.confirm is None
    assert [type(effect) for effect in effects] == [
        SaveState,
        Render,
        ControlPower,
    ]
    assert renders(effects=effects) == [NoticeView(title='Restarting')]
    assert effects[2] == ControlPower(action=PowerAction.RESTART)


def test_shut_down_top_press_cancels() -> None:
    """A top press on the confirm goes back to the menu."""
    state, _ = step(opened_at(row=MenuRow.SHUT_DOWN), DOWN)
    state, effects = step(state, UP)
    assert state.panel.confirm is None
    assert isinstance(renders(effects=effects)[0], MenuView)
    assert not [e for e in effects if isinstance(e, ControlPower)]


def test_confirm_times_out_after_60s() -> None:
    """A confirm left alone cancels itself after 60 s."""
    state, _ = step(opened_at(row=MenuRow.RESET), DOWN)
    state, _ = step(
        state, Tick(now=START + CONFIRM_WAIT - timedelta(seconds=1)),
    )
    assert state.panel.confirm is not None
    state, effects = step(state, Tick(now=START + CONFIRM_WAIT))
    assert state.panel.confirm is None
    assert isinstance(renders(effects=effects)[0], MenuView)


def test_phone_reset_asks_on_panel() -> None:
    """A reset asked for from a phone waits for a bottom press."""
    asked = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    state, effects = step(pinned(), asked)
    assert state.panel.menu is None
    assert state.panel.confirm is not None
    assert state.panel.confirm.origin is Origin.PHONE
    view = renders(effects=effects)[0]
    assert isinstance(view, ConfirmView)
    assert 'Asked for from a phone on your Wi-Fi.' in view.lines
    assert device_status(state=state).asked is ConfirmAction.RESET_SETTINGS
    _, effects = step(state, DOWN)
    assert ResetDevice(kind=ResetKind.SETTINGS) in effects


def test_phone_erase_confirmed() -> None:
    """Erase everything from a phone erases on a bottom press."""
    state, _ = step(pinned(), ConfirmAsked(action=ConfirmAction.ERASE))
    _, effects = step(state, DOWN)
    assert ResetDevice(kind=ResetKind.ERASE) in effects


def test_second_ask_ignored() -> None:
    """A second ask while one shows changes nothing."""
    state, _ = step(pinned(), ConfirmAsked(action=ConfirmAction.ERASE))
    again = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    assert step(state, again) == (state, ())


def test_ask_ignored_while_powering() -> None:
    """An ask while restarting changes nothing."""
    state, _ = run(
        state=opened_at(row=MenuRow.RESTART), events=(DOWN, DOWN),
    )
    asked = ConfirmAsked(action=ConfirmAction.ERASE)
    assert step(state, asked) == (state, ())


KNOB_EVENTS = st.one_of(
    st.sampled_from(
        [
            BOTH,
            DOWN,
            UP,
            KnobHeld(knob=Knob.TOP),
            KnobHeld(knob=Knob.BOTTOM),
        ],
    ),
    st.builds(
        KnobTurned,
        knob=st.sampled_from(Knob),
        steps=st.integers(min_value=-9, max_value=9),
    ),
    st.builds(
        KnobHeldTurned,
        knob=st.sampled_from(Knob),
        steps=st.integers(min_value=-9, max_value=9),
    ),
    st.builds(ConfirmAsked, action=st.sampled_from(ConfirmAction)),
    st.builds(
        Tick,
        now=st.integers(min_value=0, max_value=200).map(
            lambda seconds: START + timedelta(seconds=seconds),
        ),
    ),
)


@given(events=st.lists(KNOB_EVENTS, max_size=30))
def test_menu_invariants(events: list[Event]) -> None:
    """The cursor stays on the rows; power and resets need a confirm."""
    state = pinned()
    for event in events:
        shown = state.last_view
        state, effects = step(state, event)
        if state.panel.menu is not None:
            assert 0 <= state.panel.menu.cursor <= 7
        if any(isinstance(e, ControlPower | ResetDevice) for e in effects):
            assert event == DOWN
            assert isinstance(shown, ConfirmView)
        if not state.running or state.power is not None:
            return
