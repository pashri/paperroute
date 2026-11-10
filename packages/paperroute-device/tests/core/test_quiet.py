"""Tests for quiet hours in both modes."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Final

from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import MenuRow, PowerAction
from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import KitState
from paperroute_device.core.panel_views import PlainSleepView
from paperroute_device.core.quiet import in_window
from paperroute_device.core.sleep import SLEEP_BAND
from tests.core.fake_app import (
    BOTH,
    DOWN,
    START,
    FakeState,
    drawn,
    kit_step,
    renders,
)
from tests.core.test_menu import opened_at

QUIET_NOW: Final[DeviceSettings] = DeviceSettings(
    my_hours=Window(start=time(7), end=time(9)),
)
APP_MODE: Final[DeviceSettings] = replace(QUIET_NOW, quiet_mode=QuietMode.APP)
LATER: Final[Tick] = Tick(now=START + timedelta(seconds=30))


def with_settings(
    *, kit: KitState[FakeState], settings: DeviceSettings,
) -> KitState[FakeState]:
    """
    Give a kit state other device settings.

    Parameters
    ----------
    kit : KitState[FakeState]
        Kit state.
    settings : DeviceSettings
        The settings.

    Returns
    -------
    KitState[FakeState]
        The kit state with them.
    """
    return replace(kit, device=replace(kit.device, settings=settings))


def with_app(
    *,
    kit: KitState[FakeState],
    sleepy: bool = True,
    quiet: bool = False,
) -> KitState[FakeState]:
    """
    Set the fake app's sleep flags.

    Parameters
    ----------
    kit : KitState[FakeState]
        Kit state.
    sleepy : bool
        What `may_sleep` says.
    quiet : bool
        What `is_quiet` says.

    Returns
    -------
    KitState[FakeState]
        The kit state with the app's flags set.
    """
    app = replace(kit.app, sleepy=sleepy, quiet=quiet)
    return replace(kit, app=app)


def test_window_wakes_quiet_sleep() -> None:
    """A quiet sleep wakes once the app no longer lets the screen sleep."""
    kit, effects = kit_step(
        kit=with_settings(kit=drawn(), settings=QUIET_NOW), event=LATER,
    )
    assert kit.device.panel.asleep
    assert kit.device.panel.by_quiet
    assert renders(effects=effects) == [PlainSleepView(band=SLEEP_BAND)]
    tick = Tick(now=LATER.now + timedelta(seconds=30))
    kit, effects = kit_step(kit=with_app(kit=kit, sleepy=False), event=tick)
    assert not kit.device.panel.asleep
    assert renders(effects=effects) == ['view:live']


def test_quiet_waits_for_app() -> None:
    """Quiet hours do not sleep the screen while the app says no."""
    kit = with_settings(kit=drawn(), settings=QUIET_NOW)
    kit = with_app(kit=kit, sleepy=False)
    kit, effects = kit_step(kit=kit, event=LATER)
    assert not kit.device.panel.asleep
    assert not renders(effects=effects)


def test_app_mode_asks_is_quiet() -> None:
    """In the app's mode `is_quiet` decides, whatever My hours say."""
    kit = with_settings(kit=drawn(), settings=APP_MODE)
    after, _ = kit_step(kit=kit, event=LATER)
    assert not after.device.panel.asleep
    after, _ = kit_step(kit=with_app(kit=kit, quiet=True), event=LATER)
    assert after.device.panel.asleep
    outside = replace(APP_MODE, my_hours=Window(start=time(1), end=time(2)))
    kit = with_settings(kit=drawn(), settings=outside)
    kit = with_app(kit=kit, quiet=True)
    after, _ = kit_step(kit=kit, event=LATER)
    assert after.device.panel.asleep


def at(*, hour: int, minute: int = 0) -> Tick:
    """
    Tick at a time of day on START's date or the next.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.

    Returns
    -------
    Tick
        A tick after START at that time of day.
    """
    moment = datetime.combine(START.date(), time(hour, minute), START.tzinfo)
    if moment <= START:
        moment += timedelta(days=1)
    return Tick(now=moment)


def test_my_hours_wrap_midnight() -> None:
    """My hours from 23:00 to 05:30 cover 01:00 and end at 05:30."""
    window = Window(start=time(23), end=time(5, 30))
    assert in_window(moment=time(1), window=window)
    assert in_window(moment=time(23), window=window)
    assert not in_window(moment=time(5, 30), window=window)
    assert not in_window(moment=time(12), window=window)
    assert not in_window(moment=time(9), window=Window(time(9), time(9)))
    kit = with_settings(kit=drawn(), settings=DeviceSettings(my_hours=window))
    kit, _ = kit_step(kit=kit, event=at(hour=23, minute=30))
    assert kit.device.panel.asleep
    kit, _ = kit_step(kit=kit, event=at(hour=1))
    assert kit.device.panel.asleep
    kit, _ = kit_step(kit=kit, event=at(hour=5, minute=30))
    assert not kit.device.panel.asleep


def test_sleep_now_not_ended_by_tick() -> None:
    """A Sleep now sleep outside quiet hours stays asleep on a tick."""
    kit, _ = kit_step(kit=opened_at(row=MenuRow.SLEEP), event=DOWN)
    assert kit.device.panel.asleep
    assert not kit.device.panel.by_quiet
    kit, effects = kit_step(kit=kit, event=LATER)
    assert kit.device.panel.asleep
    assert not renders(effects=effects)


def test_quiet_waits_for_open_menu() -> None:
    """Quiet hours leave an open menu alone."""
    kit, _ = kit_step(kit=drawn(), event=BOTH)
    kit = with_settings(kit=kit, settings=QUIET_NOW)
    kit, _ = kit_step(kit=kit, event=LATER)
    assert not kit.device.panel.asleep
    assert kit.device.panel.menu is not None


def test_quiet_waits_for_power() -> None:
    """Quiet hours never sleep the screen during a power action."""
    off = PowerRequested(action=PowerAction.OFF)
    kit, _ = kit_step(kit=drawn(), event=off)
    kit, effects = kit_step(
        kit=with_settings(kit=kit, settings=QUIET_NOW), event=LATER,
    )
    assert not kit.device.panel.asleep
    assert not renders(effects=effects)
