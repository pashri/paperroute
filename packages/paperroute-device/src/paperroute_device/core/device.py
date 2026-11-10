"""Handlers for the device's own settings, power, keys, clock and probe."""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Final

from paperroute_device.core.app import App
from paperroute_device.core.device_events import ApiKeyChanged, PowerRequested
from paperroute_device.core.device_model import (
    ConfirmAction,
    DeviceStatus,
    Origin,
    PanelScreen,
    PowerAction,
    ResetKind,
)
from paperroute_device.core.device_state import (
    DeviceState,
    DeviceStep,
    KitSaved,
    clock_waiting,
    elapsed,
)
from paperroute_device.core.kit_effects import (
    ControlPower,
    ProbeDevice,
    Render,
    ResetDevice,
    SaveState,
    Stop,
)
from paperroute_device.core.kit_events import (
    DeviceChange,
    DeviceProbed,
    DeviceProbeFailed,
    PowerFailed,
)
from paperroute_device.core.kit_state import (
    SAVE_WAIT,
    KitState,
    KitStep,
    with_device,
)
from paperroute_device.core.panel_views import NoticeView
from paperroute_device.core.wifi import setup_closed

NOTICE: Final[dict[PowerAction, str]] = {
    PowerAction.OFF: 'Shutting down',
    PowerAction.RESTART: 'Restarting',
}
RESET_NOTICE: Final[dict[ResetKind, str]] = {
    ResetKind.SETTINGS: 'Resetting settings',
    ResetKind.ERASE: 'Erasing everything',
}
RESET_DETAIL: Final[str] = 'paperroute starts again by itself'
PROBE_EVERY: Final[timedelta] = timedelta(seconds=30)
WATCH_EVERY: Final[timedelta] = timedelta(seconds=5)
CLOCK_WAIT: Final[timedelta] = timedelta(minutes=2)


def kit_saved[S](*, kit: KitState[S], app: App[S]) -> KitSaved:
    """
    Take what the device keeps between restarts.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.

    Returns
    -------
    KitSaved
        The device settings and what the app keeps.
    """
    return KitSaved(
        device=kit.device.settings, app=app.saved(state=kit.app),
    )


def saved_effects[S](
    *, kit: KitState[S], app: App[S], wait: bool = True,
) -> KitStep[S]:
    """
    Save when what is kept differs from what was last written.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    wait : bool
        Hold the save until `SAVE_WAIT` after the last change, unless
        nothing was ever written.

    Returns
    -------
    KitStep[S]
        Kit state with the save recorded, and a SaveState; unchanged when
        nothing is due.
    """
    current = kit_saved(kit=kit, app=app)
    if current == kit.saved:
        return kit, ()
    if wait and kit.saved is not None and not elapsed(
        since=kit.changed_at, now=kit.device.now, span=SAVE_WAIT,
    ):
        return kit, ()
    return replace(kit, saved=current), (SaveState(saved=current),)


def on_device_settings(
    *, device: DeviceState, change: DeviceChange,
) -> DeviceStep:
    """
    Store the device settings given; the next tick saves them.

    Parameters
    ----------
    device : DeviceState
        Device state.
    change : DeviceChange
        The new settings; a None field is left as it is.

    Returns
    -------
    DeviceStep
        Device state with the settings stored, and a full Render of the
        last view when rotate changed and a view has been drawn.
    """
    given = {
        item.name: getattr(change, item.name)
        for item in fields(change)
        if getattr(change, item.name) is not None
    }
    settings = replace(device.settings, **given)
    turned = settings.rotate != device.settings.rotate
    after = replace(device, settings=settings)
    if turned and device.last_view is not None:
        return after, (Render(view=device.last_view, full=True),)
    return after, ()


def on_power[S](
    *, kit: KitState[S], app: App[S], event: PowerRequested,
) -> KitStep[S]:
    """
    Save, draw the notice, then shut down or restart.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    event : PowerRequested
        The action asked for.

    Returns
    -------
    KitStep[S]
        Kit state with the action under way, and a SaveState, a Render of
        the notice and a ControlPower, in that order, after any Wi-Fi
        setup is stopped; nothing when an action or a reset is already
        under way.
    """
    device = kit.device
    if device.power is not None or device.panel.resetting is not None:
        return kit, ()
    device, stops = setup_closed(device=device)
    kit = with_device(kit=kit, device=device)
    saved = kit_saved(kit=kit, app=app)
    view = NoticeView(title=NOTICE[event.action])
    device = replace(device, power=event.action, last_view=view)
    return replace(kit, device=device, saved=saved), (
        *stops,
        SaveState(saved=saved),
        Render(view=view, full=True),
        ControlPower(action=event.action),
    )


def on_power_failed(*, device: DeviceState, event: PowerFailed) -> DeviceState:
    """
    Give up on a power action.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : PowerFailed
        The action that failed.

    Returns
    -------
    DeviceState
        Device state with no action under way; unchanged when the failed
        action is not the one under way. The caller redraws.
    """
    if device.power is not event.action:
        return device
    return replace(device, power=None)


def on_reset(*, device: DeviceState, kind: ResetKind) -> DeviceStep:
    """
    Draw the reset notice, then reset; nothing is saved from here on.

    Parameters
    ----------
    device : DeviceState
        Device state.
    kind : ResetKind
        The reset asked for.

    Returns
    -------
    DeviceStep
        Device state with the reset under way, and a Render of the notice
        and a ResetDevice, in that order, after any Wi-Fi setup is
        stopped; nothing while a power action or a reset is under way.
    """
    if device.power is not None or device.panel.resetting is not None:
        return device, ()
    device, stops = setup_closed(device=device)
    view = NoticeView(title=RESET_NOTICE[kind], detail=RESET_DETAIL)
    panel = replace(device.panel, resetting=kind)
    device = replace(device, panel=panel, last_view=view)
    notice = Render(view=view, full=True)
    return device, (*stops, notice, ResetDevice(kind=kind))


def on_reset_done[S](*, kit: KitState[S]) -> KitStep[S]:
    """
    Stop once a reset has finished, so the board starts again fresh.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.

    Returns
    -------
    KitStep[S]
        Stopped kit state, and a Stop.
    """
    return replace(kit, running=False), (Stop(),)


def on_key(*, device: DeviceState, event: ApiKeyChanged) -> DeviceState:
    """
    Store which keys are missing.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : ApiKeyChanged
        Which keys are missing.

    Returns
    -------
    DeviceState
        Device state with the missing keys noted. The caller redraws.
    """
    panel = replace(device.panel, missing_keys=event.missing)
    return replace(device, panel=panel)


def clock_checked(
    *, device: DeviceState, now: datetime, synced: bool, zone: tzinfo,
) -> DeviceState:
    """
    Start, give up or end the wait for the clock to be synchronised.

    Only the first frame waits: an unsynced tick after a frame is drawn
    starts no wait.

    Parameters
    ----------
    device : DeviceState
        Device state before the tick.
    now : datetime
        The tick's time.
    synced : bool
        Whether the tick found the clock synchronised.
    zone : tzinfo
        Local time zone the wait's end is given in.

    Returns
    -------
    DeviceState
        Device state at the tick's time: waiting until `CLOCK_WAIT` from
        now after the first unsynced tick; given up, with a probe due, on
        the tick the wait runs out; `clock_set` on the first synced tick
        after unsynced ones; otherwise only the time moves on.
    """
    after = replace(device, now=now, clock=replace(device.clock, known=True))
    waiting = device.clock.wait_until is not None
    if synced:
        return clock_set(device=after) if waiting else after
    if (
        waiting
        and clock_waiting(device=device)
        and not clock_waiting(device=after)
    ):
        panel = replace(after.panel, probed_at=None)
        clock = replace(after.clock, gave_up=True)
        return replace(after, clock=clock, panel=panel)
    if waiting or device.last_view is not None:
        return after
    until = (now.astimezone(UTC) + CLOCK_WAIT).astimezone(zone)
    return replace(after, clock=replace(after.clock, wait_until=until))


def clock_set(*, device: DeviceState) -> DeviceState:
    """
    Start afresh once the clock is synchronised, as it may have jumped.

    Parameters
    ----------
    device : DeviceState
        Device state at the first synced tick's time.

    Returns
    -------
    DeviceState
        Device state with no clock wait, the clock noted as set now and a
        probe due. The app sees `ClockWait.set_at` change through
        `App.on_device`.
    """
    clock = replace(
        device.clock, wait_until=None, gave_up=False, set_at=device.now,
    )
    panel = replace(device.panel, probed_at=None)
    return replace(device, clock=clock, panel=panel)


def probe_due(*, device: DeviceState) -> bool:
    """
    Say whether the device's facts should be asked for now.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    bool
        True on the first tick, once `PROBE_EVERY` has passed, or once
        `WATCH_EVERY` has passed while the Device screen shows; never
        while a probe younger than `PROBE_EVERY` has not answered, nor
        while Wi-Fi setup is open.
    """
    panel = device.panel
    if panel.wifi is not None:
        return False
    if panel.probed_at is None:
        return True
    since = device.now - panel.probed_at
    if panel.probing and since < PROBE_EVERY:
        return False
    menu = panel.menu
    watched = menu is not None and menu.screen is PanelScreen.DEVICE
    return since >= (WATCH_EVERY if watched else PROBE_EVERY)


def probe_effects(*, device: DeviceState) -> DeviceStep:
    """
    Ask for the device's facts when a probe is due.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceStep
        Device state with the probe time noted and marked running, and a
        ProbeDevice, when `probe_due`; unchanged otherwise.
    """
    if not probe_due(device=device):
        return device, ()
    panel = replace(device.panel, probed_at=device.now, probing=True)
    return replace(device, panel=panel), (ProbeDevice(),)


def on_probed(
    *, device: DeviceState, event: DeviceProbed | DeviceProbeFailed,
) -> DeviceState:
    """
    Store the device's facts and which keys are missing.

    Parameters
    ----------
    device : DeviceState
        Device state.
    event : DeviceProbed | DeviceProbeFailed
        What the probe found, or that it failed.

    Returns
    -------
    DeviceState
        Device state with the facts and key state; only marked answered
        when the probe failed.
    """
    device = replace(device, panel=replace(device.panel, probing=False))
    if isinstance(event, DeviceProbeFailed):
        return device
    panel = replace(device.panel, facts=event.facts)
    return on_key(
        device=replace(device, panel=panel),
        event=ApiKeyChanged(missing=event.facts.missing_keys),
    )


def device_status(*, device: DeviceState) -> DeviceStatus:
    """
    Gather what the device reports about itself.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    DeviceStatus
        The keys not set, latest facts, what a phone asked for that
        waits on the panel and Wi-Fi setup.
    """
    return DeviceStatus(
        missing_keys=device.panel.missing_keys,
        facts=device.panel.facts,
        asked=phone_asked(device=device),
        setup=device.panel.wifi,
    )


def phone_asked(*, device: DeviceState) -> ConfirmAction | None:
    """
    Say what a phone asked for that waits on the panel.

    Parameters
    ----------
    device : DeviceState
        Device state.

    Returns
    -------
    ConfirmAction | None
        The action of a confirm a phone asked for, or None.
    """
    confirm = device.panel.confirm
    if confirm is None or confirm.origin is not Origin.PHONE:
        return None
    return confirm.action
