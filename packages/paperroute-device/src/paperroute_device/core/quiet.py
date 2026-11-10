"""Quiet hours: when the screen sleeps by itself, and when it wakes."""

from dataclasses import replace
from datetime import time, timedelta

from paperroute_device.core.app import App
from paperroute_device.core.device_settings import QuietMode, Window
from paperroute_device.core.device_state import DeviceState, elapsed
from paperroute_device.core.kit_state import KitState, KitStep, with_device
from paperroute_device.core.sleep import awake, slept


def in_window(*, moment: time, window: Window) -> bool:
    """
    Say whether a time of day falls inside a window.

    Parameters
    ----------
    moment : time
        The time of day.
    window : Window
        The window; it wraps midnight when it ends before it starts.

    Returns
    -------
    bool
        True from the start up to the end; never for a window whose start
        and end are the same.
    """
    if window.start <= window.end:
        return window.start <= moment < window.end
    return moment >= window.start or moment < window.end


def quiet_due[S](*, device: DeviceState, app: App[S], state: S) -> bool:
    """
    Say whether it is quiet hours now and the app lets the screen sleep.

    Parameters
    ----------
    device : DeviceState
        Device state.
    app : App[S]
        The app.
    state : S
        The app's state.

    Returns
    -------
    bool
        By My hours, whether now is inside them; by the app's mode,
        whether `App.is_quiet` says so; in both, only while
        `App.may_sleep` agrees.
    """
    settings = device.settings
    if settings.quiet_mode is QuietMode.MY_HOURS:
        quiet = in_window(moment=device.now.time(), window=settings.my_hours)
    else:
        quiet = app.is_quiet(state=state, now=device.now)
    return quiet and app.may_sleep(state=state, now=device.now)


def panel_may_sleep(*, device: DeviceState) -> bool:
    """
    Say whether quiet hours may put the screen to sleep now.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    bool
        True when awake with no wake running, no confirm, menu or Wi-Fi
        setup open, and no power action or reset under way.
    """
    panel = device.panel
    if panel.asleep or device.power is not None:
        return False
    if panel.awake_until is not None and panel.awake_until > device.now:
        return False
    return (panel.confirm, panel.menu, panel.wifi, panel.resetting) == (
        None, None, None, None,
    )


def quiet_effects[S](*, kit: KitState[S], app: App[S]) -> KitStep[S]:
    """
    Sleep at the start of quiet hours and wake at their end.

    Parameters
    ----------
    kit : KitState[S]
        Kit state at the tick's time.
    app : App[S]
        The app.

    Returns
    -------
    KitStep[S]
        A quiet sleep that is no longer due wakes; quiet hours sleep a
        screen that `panel_may_sleep`; a wake run out outside quiet
        hours is forgotten. A Sleep now sleep is left alone. The caller
        redraws.
    """
    device = kit.device
    panel = device.panel
    due = quiet_due(device=device, app=app, state=kit.app)
    if panel.asleep:
        if panel.by_quiet and not due:
            return with_device(kit=kit, device=awake(device=device)), ()
        return kit, ()
    if due and panel_may_sleep(device=device):
        asleep = slept(device=device, by_quiet=True)
        return with_device(kit=kit, device=asleep), ()
    if panel.awake_until is not None and not due and elapsed(
        since=panel.awake_until, now=device.now, span=timedelta(),
    ):
        panel = replace(panel, awake_until=None)
        return with_device(kit=kit, device=replace(device, panel=panel)), ()
    return kit, ()
