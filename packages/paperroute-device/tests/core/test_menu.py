"""Tests for the kit's Settings menu and confirming on the device."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from paperroute_device.core.device import device_status
from paperroute_device.core.device_events import ConfirmAsked
from paperroute_device.core.device_model import (
    AppRow,
    ConfirmAction,
    Menu,
    MenuRow,
    Origin,
    PanelScreen,
    PowerAction,
    ResetKind,
    menu_rows,
)
from paperroute_device.core.kit_effects import (
    ControlPower,
    ProbeDevice,
    Render,
    ResetDevice,
    SaveState,
)
from paperroute_device.core.kit_events import DeviceProbed, Tick
from paperroute_device.core.kit_state import KitState
from paperroute_device.core.menu import CONFIRM_WAIT, MENU_IDLE
from paperroute_device.core.panel_views import (
    ConfirmCopy,
    ConfirmView,
    MenuView,
    NoticeView,
    PhoneView,
)
from paperroute_device.core.reducer import step
from paperroute_device_testkit.device_fakes import device_facts
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobTurned,
)
from tests.core.fake_app import (
    APP,
    BOTH,
    DO_IT,
    DOWN,
    START,
    UP,
    ZONE,
    FakeApp,
    FakeState,
    drawn,
    kit_step,
    renders,
    run,
)

ROWS: Final[tuple[MenuRow | AppRow, ...]] = menu_rows(app_rows=APP.menu_rows)


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


def opened_at(*, row: MenuRow | AppRow) -> KitState[FakeState]:
    """
    Open the menu with the cursor on a row.

    Parameters
    ----------
    row : MenuRow | AppRow
        The row.

    Returns
    -------
    KitState[FakeState]
        The kit state with the menu open.
    """
    kit, _ = run(kit=drawn(), events=(BOTH, turn(steps=ROWS.index(row))))
    return kit


def unmoved(*, kit: KitState[FakeState], event: object) -> bool:
    """
    Say whether an event leaves the device and the app's view alone.

    Parameters
    ----------
    kit : KitState[FakeState]
        Kit state.
    event : object
        The event.

    Returns
    -------
    bool
        True when the device state is unchanged, the app saw no knob
        and nothing is asked for.
    """
    after, effects = kit_step(kit=kit, event=event)
    return (
        after.device == kit.device
        and after.app.knobs == kit.app.knobs
        and not effects
    )


def test_both_held_opens_menu() -> None:
    """Holding both opens the menu over the live view and the app's menu."""
    kit, effects = kit_step(kit=drawn(), event=BOTH)
    assert kit.device.panel.menu == Menu()
    assert isinstance(renders(effects=effects)[0], MenuView)
    chosen = drawn(app=FakeState(menu=True))
    kit, _ = run(kit=chosen, events=(BOTH, UP))
    assert kit.device.panel.menu is None
    assert kit.app.menu
    assert kit.device.last_view == 'view:live'
    assert not kit.app.knobs


def test_cursor_moves_and_clamps() -> None:
    """The cursor stays on the rows."""
    kit, effects = run(kit=drawn(), events=(BOTH, turn(steps=-1)))
    assert kit.device.panel.menu == Menu(cursor=0)
    assert not effects
    kit, _ = kit_step(kit=kit, event=turn(steps=20))
    assert kit.device.panel.menu == Menu(cursor=7)


def test_menu_scrolls_to_reset() -> None:
    """The last row scrolls the list by one."""
    kit = opened_at(row=MenuRow.RESET)
    assert isinstance(kit.device.last_view, MenuView)
    assert kit.device.last_view.first == 1
    assert kit.device.last_view.cursor == 7


def test_top_press_closes_menu() -> None:
    """A top press closes the menu and draws the live view."""
    kit, effects = run(kit=drawn(), events=(BOTH, UP))
    assert kit.device.panel.menu is None
    assert renders(effects=effects) == ['view:live']


def test_menu_ignores_holds_and_top_turn() -> None:
    """Holds, top turns and held turns do nothing in the menu."""
    kit, _ = kit_step(kit=drawn(), event=BOTH)
    for event in (
        KnobHeld(knob=Knob.TOP),
        KnobHeld(knob=Knob.BOTTOM),
        KnobTurned(knob=Knob.TOP, steps=1),
        KnobHeldTurned(knob=Knob.BOTTOM, steps=1),
        BOTH,
    ):
        assert unmoved(kit=kit, event=event)


def test_bottom_hold_still_pins_with_menu_closed() -> None:
    """A bottom hold goes to the app once the menu is closed."""
    before, _ = run(kit=drawn(), events=(BOTH, UP))
    after, _ = kit_step(kit=before, event=KnobHeld(knob=Knob.BOTTOM))
    assert after.app.knobs == [KnobHeld(knob=Knob.BOTTOM)]


def answered(*, seconds: int) -> KitState[FakeState]:
    """
    Answer the first tick's probe, then open the menu on Device.

    Parameters
    ----------
    seconds : int
        Seconds after START the menu opens.

    Returns
    -------
    KitState[FakeState]
        The kit state with the cursor on Device.
    """
    kit, _ = run(
        kit=drawn(),
        events=(
            DeviceProbed(facts=device_facts()),
            Tick(now=START + timedelta(seconds=seconds)),
            BOTH,
            turn(steps=1),
        ),
    )
    return kit


def test_device_row_opens_device_screen_and_probes() -> None:
    """The Device row opens its screen and asks for the facts."""
    kit, effects = kit_step(kit=answered(seconds=5), event=DOWN)
    assert kit.device.panel.menu == Menu(screen=PanelScreen.DEVICE, cursor=1)
    assert ProbeDevice() in effects
    kit, _ = kit_step(kit=kit, event=UP)
    assert kit.device.panel.menu == Menu(cursor=1)


def probes(*, effects: tuple[object, ...]) -> int:
    """
    Count the probes asked for.

    Parameters
    ----------
    effects : tuple[object, ...]
        Effects.

    Returns
    -------
    int
        How many ProbeDevice effects there are.
    """
    return sum(isinstance(effect, ProbeDevice) for effect in effects)


def test_device_screen_probes_every_5s_one_at_a_time() -> None:
    """The Device screen probes every 5 s, never over a running probe."""
    kit, effects = kit_step(kit=answered(seconds=5), event=DOWN)
    assert probes(effects=effects) == 1
    kit, effects = kit_step(
        kit=kit, event=Tick(now=START + timedelta(seconds=9)),
    )
    assert probes(effects=effects) == 0
    kit, effects = kit_step(
        kit=kit, event=Tick(now=START + timedelta(seconds=10)),
    )
    assert probes(effects=effects) == 0
    kit, _ = kit_step(kit=kit, event=DeviceProbed(facts=device_facts()))
    kit, effects = kit_step(
        kit=kit, event=Tick(now=START + timedelta(seconds=10)),
    )
    assert probes(effects=effects) == 1


def test_menu_closes_after_two_idle_minutes() -> None:
    """The menu closes to the live view after 2 minutes with no input."""
    kit, _ = run(kit=drawn(), events=(BOTH, turn(steps=2), DOWN))
    kit, _ = kit_step(
        kit=kit, event=Tick(now=START + MENU_IDLE - timedelta(seconds=1)),
    )
    assert kit.device.panel.menu is not None
    kit, effects = kit_step(kit=kit, event=Tick(now=START + MENU_IDLE))
    assert kit.device.panel.menu is None
    assert renders(effects=effects) == ['view:live']


@pytest.mark.parametrize(
    'action', [ConfirmAction.RESTART, ConfirmAction.SHUT_DOWN],
)
def test_phone_power_not_asked_on_panel(action: ConfirmAction) -> None:
    """Restart and Shut down from a phone never show a confirm."""
    assert unmoved(kit=drawn(), event=ConfirmAsked(action=action))


def test_phone_row_shows_addresses() -> None:
    """The phone screen opens the IP, and names paperroute.local."""
    facts = device_facts()
    wifi = replace(facts.wifi, managed=True, ssid='Home', ip='192.168.1.40')
    kit = opened_at(row=MenuRow.PHONE)
    panel = replace(kit.device.panel, facts=replace(facts, wifi=wifi))
    kit = replace(kit, device=replace(kit.device, panel=panel))
    _, effects = kit_step(kit=kit, event=DOWN)
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
    kit, effects = kit_step(kit=opened_at(row=MenuRow.RESTART), event=DOWN)
    assert isinstance(renders(effects=effects)[0], ConfirmView)
    kit, effects = kit_step(kit=kit, event=DOWN)
    assert kit.device.panel.menu is None
    assert kit.device.panel.confirm is None
    assert [type(effect) for effect in effects] == [
        SaveState,
        Render,
        ControlPower,
    ]
    assert renders(effects=effects) == [NoticeView(title='Restarting')]
    assert effects[2] == ControlPower(action=PowerAction.RESTART)


def test_shut_down_top_press_cancels() -> None:
    """A top press on the confirm goes back to the menu."""
    kit, _ = kit_step(kit=opened_at(row=MenuRow.SHUT_DOWN), event=DOWN)
    kit, effects = kit_step(kit=kit, event=UP)
    assert kit.device.panel.confirm is None
    assert isinstance(renders(effects=effects)[0], MenuView)
    assert not [e for e in effects if isinstance(e, ControlPower)]


def test_confirm_times_out_after_60s() -> None:
    """A confirm left alone cancels itself after 60 s."""
    kit, _ = kit_step(kit=opened_at(row=MenuRow.RESET), event=DOWN)
    kit, _ = kit_step(
        kit=kit, event=Tick(now=START + CONFIRM_WAIT - timedelta(seconds=1)),
    )
    assert kit.device.panel.confirm is not None
    kit, effects = kit_step(kit=kit, event=Tick(now=START + CONFIRM_WAIT))
    assert kit.device.panel.confirm is None
    assert isinstance(renders(effects=effects)[0], MenuView)


def test_phone_reset_asks_on_panel() -> None:
    """A reset asked for from a phone waits for a bottom press."""
    asked = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    kit, effects = kit_step(kit=drawn(), event=asked)
    panel = kit.device.panel
    assert panel.menu is None
    assert panel.confirm is not None
    assert panel.confirm.origin is Origin.PHONE
    view = renders(effects=effects)[0]
    assert isinstance(view, ConfirmView)
    assert 'Asked for from a phone on your Wi-Fi.' in view.lines
    asked_for = device_status(device=kit.device).asked
    assert asked_for is ConfirmAction.RESET_SETTINGS
    _, effects = kit_step(kit=kit, event=DOWN)
    assert ResetDevice(kind=ResetKind.SETTINGS) in effects


def test_phone_erase_confirmed() -> None:
    """Erase everything from a phone erases on a bottom press."""
    asked = ConfirmAsked(action=ConfirmAction.ERASE)
    kit, _ = kit_step(kit=drawn(), event=asked)
    _, effects = kit_step(kit=kit, event=DOWN)
    assert ResetDevice(kind=ResetKind.ERASE) in effects


def test_second_ask_ignored() -> None:
    """A second ask while one shows changes nothing."""
    asked = ConfirmAsked(action=ConfirmAction.ERASE)
    kit, _ = kit_step(kit=drawn(), event=asked)
    again = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    assert unmoved(kit=kit, event=again)


def test_ask_ignored_while_powering() -> None:
    """An ask while restarting changes nothing."""
    kit, _ = run(kit=opened_at(row=MenuRow.RESTART), events=(DOWN, DOWN))
    assert unmoved(kit=kit, event=ConfirmAsked(action=ConfirmAction.ERASE))


def test_app_row_calls_on_row_then_shows_device() -> None:
    """The app's row runs `App.on_row`, then shows the Device screen."""
    kit, effects = kit_step(kit=opened_at(row=DO_IT), event=DOWN)
    assert kit.app.rows == 1
    assert kit.device.panel.menu == Menu(
        screen=PanelScreen.DEVICE, cursor=ROWS.index(DO_IT),
    )
    assert effects[0] == 'work'
    assert [type(effect) for effect in effects[1:]] == [Render]


class CopyApp(FakeApp):
    """The fake app with its own confirm copy."""

    confirm_copy: ConfirmCopy = {
        action: f'{action.value} happens' for action in ConfirmAction
    }


def test_confirm_uses_the_apps_copy() -> None:
    """A confirm says what happens in the app's words."""
    asked = ConfirmAsked(action=ConfirmAction.ERASE)
    _, effects = step(kit=drawn(), app=CopyApp(), zone=ZONE, event=asked)
    view = renders(effects=effects)[0]
    assert isinstance(view, ConfirmView)
    assert view.lines[-1] == 'erase happens'


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
def test_menu_invariants(events: list[object]) -> None:
    """The cursor stays on the rows; power and resets need a confirm."""
    kit = drawn()
    for event in events:
        shown = kit.device.last_view
        kit, effects = kit_step(kit=kit, event=event)
        if kit.device.panel.menu is not None:
            assert 0 <= kit.device.panel.menu.cursor <= 7
        if any(isinstance(e, ControlPower | ResetDevice) for e in effects):
            assert event == DOWN
            assert isinstance(shown, ConfirmView)
        if not kit.running or kit.device.power is not None:
            return


def test_menu_rows_read_as_before() -> None:
    """The menu lists the kit's rows around the app's, in order."""
    assert tuple(
        row.value if isinstance(row, MenuRow) else row.label for row in ROWS
    ) == (
        'Wi-Fi',
        'Device',
        'Open on your phone',
        'Sleep now',
        'Do it',
        'Restart',
        'Shut down',
        'Reset settings',
    )
