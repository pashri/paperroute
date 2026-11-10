"""Tests for the kit's step: the order it calls the app and its guards."""

from dataclasses import replace
from datetime import time, timedelta

from paperroute_device.core.device_events import ConfirmAsked, PowerRequested
from paperroute_device.core.device_model import (
    ConfirmAction,
    MenuRow,
    PowerAction,
    ResetKind,
    WifiPhase,
    WifiSetup,
)
from paperroute_device.core.device_settings import DeviceSettings, Window
from paperroute_device.core.device_state import KitSaved
from paperroute_device.core.kit_effects import (
    ControlPower,
    Render,
    ResetDevice,
    SaveState,
    Stop,
    StopWifiSetup,
)
from paperroute_device.core.kit_events import (
    DeviceChange,
    QuitRequested,
    SettingsPosted,
    Tick,
)
from paperroute_device.core.kit_state import SAVE_WAIT, KitState
from paperroute_device.core.panel_views import (
    ConfirmView,
    MenuView,
    NoticeView,
    PlainSleepView,
)
from paperroute_device.core.sleep import SLEEP_BAND, WAKE_FOR
from paperroute_hardware.core.knob_events import Knob, KnobHeld, KnobTurned
from tests.core.fake_app import (
    BOTH,
    DOWN,
    HELD,
    START,
    UP,
    FakeState,
    drawn,
    fresh,
    kit_step,
    renders,
    run,
)
from tests.core.test_menu import opened_at

TURN = KnobTurned(knob=Knob.BOTTOM, steps=1)
HOLD = KnobHeld(knob=Knob.BOTTOM)


def test_begin_runs_before_the_handler_and_render() -> None:
    """`begin` marks the state before `on_knob` and `render` see it."""
    kit, effects = kit_step(kit=drawn(), event=HOLD)
    assert renders(effects=effects) == ['view:held*']
    assert kit.app.view == 'held'
    assert not kit.app.marked


def test_touch_order_is_confirm_setup_menu_held_app() -> None:
    """A confirm, then setup, then the menu, then both held, then the app."""
    setup = WifiSetup(phase=WifiPhase.HOTSPOT, until=START + WAKE_FOR)
    asked = ConfirmAsked(action=ConfirmAction.ERASE)
    kit, _ = kit_step(kit=drawn(), event=asked)
    kit = replace(kit, device=replace(
        kit.device, panel=replace(kit.device.panel, wifi=setup),
    ))
    _, effects = kit_step(kit=kit, event=DOWN)
    assert ResetDevice(kind=ResetKind.ERASE) in effects
    kit = replace(kit, device=replace(
        kit.device, panel=replace(kit.device.panel, confirm=None),
    ))
    kit, effects = kit_step(kit=kit, event=UP)
    assert StopWifiSetup() in effects
    kit, effects = kit_step(kit=kit, event=TURN)
    assert kit.device.panel.menu is None
    assert kit.app.knobs == [TURN]
    kit, _ = run(kit=drawn(), events=(BOTH, TURN))
    assert kit.device.panel.menu is not None
    assert kit.device.panel.menu.cursor == 1
    assert not kit.app.knobs


def sleeping() -> KitState[FakeState]:
    """
    Put the drawn kit to sleep through the Sleep now row.

    Returns
    -------
    KitState[FakeState]
        The kit state, asleep and showing the sleep screen.
    """
    kit, _ = kit_step(kit=opened_at(row=MenuRow.SLEEP), event=DOWN)
    return kit


def test_first_touch_only_wakes() -> None:
    """A touch while asleep wakes for `WAKE_FOR`; the app never sees it."""
    kit = sleeping()
    assert kit.device.panel.asleep
    assert kit.device.last_view == PlainSleepView(band=SLEEP_BAND)
    kit, effects = kit_step(kit=kit, event=TURN)
    assert not kit.device.panel.asleep
    assert kit.device.panel.awake_until == START + WAKE_FOR
    assert not kit.app.knobs
    assert effects == (Render(view='view:live', full=True),)
    kit, _ = kit_step(kit=kit, event=TURN)
    assert kit.app.knobs == [TURN]


def test_ask_while_asleep_wakes_and_shows_confirm() -> None:
    """A phone's ask while asleep wakes and shows its confirm in one go."""
    asked = ConfirmAsked(action=ConfirmAction.RESET_SETTINGS)
    kit, effects = kit_step(kit=sleeping(), event=asked)
    assert not kit.device.panel.asleep
    assert kit.device.panel.confirm is not None
    views = renders(effects=effects)
    assert len(views) == 1
    assert isinstance(views[0], ConfirmView)


def test_quit_saves_then_stops() -> None:
    """A quit saves an unsaved change at once, then stops."""
    posted = SettingsPosted(device=DeviceChange(rotate=True), app=None)
    kit, _ = kit_step(kit=drawn(), event=posted)
    kit, effects = kit_step(kit=kit, event=QuitRequested())
    saved = KitSaved(device=DeviceSettings(rotate=True), app='live')
    assert effects == (SaveState(saved=saved), Stop())
    assert not kit.running
    kit, effects = kit_step(kit=kit, event=QuitRequested())
    assert effects == (Stop(),)
    changed = replace(kit, app=replace(kit.app, view='changed'))
    _, effects = kit_step(kit=changed, event=QuitRequested())
    assert effects == (SaveState(saved=replace(saved, app='changed')), Stop())


def test_settle_sees_the_final_device_and_app_effects() -> None:
    """`settle` gets the device after quiet hours, and the app's effects."""
    quiet = DeviceSettings(my_hours=Window(start=time(7), end=time(9)))
    kit = fresh()
    kit = replace(kit, device=replace(kit.device, settings=quiet))
    kit, effects = kit_step(kit=kit, event=Tick(now=START))
    assert kit.device.panel.asleep
    device = kit.app.device
    assert device is not None
    assert device.panel.asleep
    assert renders(effects=effects) == [PlainSleepView(band=SLEEP_BAND)]
    kit, effects = kit_step(kit=drawn(), event='other')
    assert HELD not in effects
    assert effects[0] == 'kept'
    assert kit.app.settled == 2


def test_kit_render_dropped_while_powering_and_save_while_resetting() -> None:
    """Power keeps the notice up; a reset also writes nothing more."""
    off = PowerRequested(action=PowerAction.OFF)
    kit, effects = kit_step(kit=drawn(), event=off)
    notice = NoticeView(title='Shutting down')
    assert [type(effect) for effect in effects] == [
        SaveState, Render, ControlPower,
    ]
    kit, effects = kit_step(kit=kit, event='other')
    assert effects == ('kept',)
    assert kit.device.last_view == notice
    asked = ConfirmAsked(action=ConfirmAction.ERASE)
    posted = SettingsPosted(device=DeviceChange(rotate=True), app=None)
    kit, _ = run(kit=drawn(), events=(posted, asked, DOWN))
    assert kit.device.panel.resetting is ResetKind.ERASE
    later = Tick(now=START + SAVE_WAIT + timedelta(seconds=1))
    kit, effects = kit_step(kit=kit, event=later)
    assert not [e for e in effects if isinstance(e, Render | SaveState)]
    _, effects = kit_step(kit=kit, event=QuitRequested())
    assert effects == (Stop(),)


def test_device_setting_saves_on_the_tick() -> None:
    """A posted device setting is saved `SAVE_WAIT` later, not before."""
    posted = SettingsPosted(device=DeviceChange(rotate=True), app=None)
    kit, effects = kit_step(kit=drawn(), event=posted)
    assert effects == (Render(view='view:live', full=True),)
    assert kit.changed_at == START
    early = Tick(now=START + SAVE_WAIT - timedelta(seconds=1))
    kit, effects = kit_step(kit=kit, event=early)
    assert not [e for e in effects if isinstance(e, SaveState)]
    kit, effects = kit_step(kit=kit, event=Tick(now=START + SAVE_WAIT))
    saved = KitSaved(device=DeviceSettings(rotate=True), app='live')
    assert SaveState(saved=saved) in effects
    assert kit.running
    assert kit.saved == saved


def test_noted_started_and_touched() -> None:
    """The first event notes the start; a knob event notes the touch."""
    kit, _ = kit_step(kit=fresh(), event='other')
    assert kit.device.started_at == START
    assert kit.device.touched_at is None
    later = START + timedelta(seconds=5)
    kit, _ = run(kit=kit, events=(Tick(now=later), TURN))
    assert kit.device.started_at == START
    assert kit.device.touched_at == later


def test_menu_screen_is_drawn_clear() -> None:
    """Opening the menu draws it clear; closing it draws full."""
    kit, effects = kit_step(kit=drawn(), event=BOTH)
    assert effects == (Render(view=kit.device.last_view, clear=True),)
    assert isinstance(kit.device.last_view, MenuView)
    _, effects = kit_step(kit=kit, event=UP)
    assert effects == (Render(view='view:live', full=True),)
