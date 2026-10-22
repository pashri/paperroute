"""Handlers for the device's own settings and power."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.banners import feeds_held
from paperroute.core.device_model import DeviceStatus, ResetKind
from paperroute.core.messages import (
    ApiKeyChanged,
    ControlPower,
    DeviceProbed,
    DeviceProbeFailed,
    Effect,
    FetchFeed,
    NoticeView,
    PowerAction,
    PowerFailed,
    PowerRequested,
    ProbeDevice,
    Render,
    ResetDevice,
    ResetDone,
    SaveState,
    SettingsChanged,
    Stop,
)
from paperroute.core.quota import calls_today
from paperroute.core.saved import Settings
from paperroute.core.state_model import State
from paperroute.core.state_views import saved_of, settled

NOTICE: Final[dict[PowerAction, str]] = {
    PowerAction.OFF: 'Shut down - safe to unplug',
    PowerAction.RESTART: 'Restarting',
}
RESET_NOTICE: Final[dict[ResetKind, str]] = {
    ResetKind.SETTINGS: 'Resetting settings',
    ResetKind.ERASE: 'Erasing everything',
}
RESET_DETAIL: Final[str] = 'paperroute starts again by itself'
PROBE_EVERY: Final[timedelta] = timedelta(seconds=30)


def on_settings(
    *, state: State, event: SettingsChanged
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store a changed setting; the next tick saves it.

    Parameters
    ----------
    state : State
        State.
    event : SettingsChanged
        The new setting.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the setting stored, and no effects.
    """
    settings = Settings(auto_rebuild=event.auto_rebuild)
    return replace(state, settings=settings), ()


def on_power(
    *, state: State, event: PowerRequested
) -> tuple[State, tuple[Effect, ...]]:
    """
    Save, draw the notice, then shut down or restart.

    Parameters
    ----------
    state : State
        State.
    event : PowerRequested
        The action asked for.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the action under way, and a SaveState, a Render of the
        notice and a ControlPower, in that order; nothing when an action
        or a reset is already under way.
    """
    if state.power is not None or state.panel.resetting is not None:
        return state, ()
    saved = saved_of(state=state)
    view = NoticeView(title=NOTICE[event.action])
    state = replace(
        state,
        power=event.action,
        last_view=view,
        saving=replace(state.saving, saved=saved),
    )
    return state, (
        SaveState(saved=saved),
        Render(view=view),
        ControlPower(action=event.action),
    )


def on_power_failed(
    *, state: State, event: PowerFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Give up on a power action and draw the Live view again.

    Parameters
    ----------
    state : State
        State.
    event : PowerFailed
        The action that failed.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with no action under way, and the redraw; nothing when the
        failed action is not the one under way.
    """
    if state.power is not event.action:
        return state, ()
    return settled(state=replace(state, power=None))


def on_reset(
    *, state: State, kind: ResetKind
) -> tuple[State, tuple[Effect, ...]]:
    """
    Draw the reset notice, then reset; nothing is saved from here on.

    Parameters
    ----------
    state : State
        State.
    kind : ResetKind
        The reset asked for.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the reset under way, and a Render of the notice and a
        ResetDevice, in that order; nothing while a power action or a
        reset is under way.
    """
    if state.power is not None or state.panel.resetting is not None:
        return state, ()
    view = NoticeView(title=RESET_NOTICE[kind], detail=RESET_DETAIL)
    panel = replace(state.panel, resetting=kind)
    state = replace(state, panel=panel, last_view=view)
    return state, (Render(view=view), ResetDevice(kind=kind))


def on_reset_done(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Stop once a reset has finished, so the app starts again fresh.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Stopped state, and a Stop.
    """
    return replace(state, running=False), (Stop(),)


type DeviceEvent = (
    SettingsChanged
    | PowerRequested
    | PowerFailed
    | ApiKeyChanged
    | DeviceProbed
    | DeviceProbeFailed
    | ResetDone
)


def on_device_event(
    *, state: State, event: DeviceEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store a setting, the key's state or the facts, or handle power.

    Parameters
    ----------
    state : State
        State.
    event : DeviceEvent
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; a key change or probe is held by
        `power_guard` and `quiet_guard` like any redraw.
    """
    match event:
        case SettingsChanged():
            return on_settings(state=state, event=event)
        case PowerFailed():
            return on_power_failed(state=state, event=event)
        case PowerRequested():
            return on_power(state=state, event=event)
        case ApiKeyChanged():
            result = on_key(state=state, event=event)
        case ResetDone():
            result = on_reset_done(state=state)
        case _:
            result = on_probed(state=state, event=event)
    return quiet_guard(result=power_guard(state=state, result=result))


def power_guard(
    *, state: State, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop Renders while a power action or reset is under way.

    While a reset is under way every SaveState is dropped too, so the
    state file it deletes is never written again.

    Parameters
    ----------
    state : State
        State before the event.
    result : tuple[State, tuple[Effect, ...]]
        The handler's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The result, without the dropped effects when an action or reset
        is under way, and with the notice kept as the view last drawn.
    """
    after, effects = result
    resetting = state.panel.resetting is not None
    if state.power is None and not resetting:
        return result
    dropped = (Render, SaveState) if resetting else (Render,)
    kept = tuple(
        effect for effect in effects if not isinstance(effect, dropped)
    )
    return replace(after, last_view=state.last_view), kept


def on_key(
    *, state: State, event: ApiKeyChanged
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store whether an API key is set, and redraw.

    Parameters
    ----------
    state : State
        State.
    event : ApiKeyChanged
        Whether a key is set.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the key noted, and the redraw if the view changed. A
        key that was missing and is now set makes the next tick poll.
    """
    if event.present and state.panel.key_set is False:
        state = replace(
            state,
            last_fetch=None,
            map=replace(state.map, fetched_at={}),
        )
    panel = replace(state.panel, key_set=event.present)
    return settled(state=replace(state, panel=panel))


def quiet_guard(
    *, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop every FetchFeed from a result while the feeds are held.

    Parameters
    ----------
    result : tuple[State, tuple[Effect, ...]]
        The handler's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The result, without FetchFeeds when `feeds_held` says so for the
        state after the event.
    """
    after, effects = result
    if not feeds_held(state=after):
        return result
    kept = tuple(
        effect for effect in effects if not isinstance(effect, FetchFeed)
    )
    return after, kept


def probe_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the device's facts when a probe is due.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the probe time noted, and a ProbeDevice, on the first
        tick and once `PROBE_EVERY` has passed; unchanged otherwise.
    """
    probed_at = state.panel.probed_at
    if probed_at is not None and state.now - probed_at < PROBE_EVERY:
        return state, ()
    panel = replace(state.panel, probed_at=state.now)
    return replace(state, panel=panel), (ProbeDevice(),)


def on_probed(
    *, state: State, event: DeviceProbed | DeviceProbeFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store the device's facts and whether a key is set.

    Parameters
    ----------
    state : State
        State.
    event : DeviceProbed | DeviceProbeFailed
        What the probe found, or that it failed.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the facts and key state, settled as a key change is;
        unchanged when the probe failed.
    """
    if isinstance(event, DeviceProbeFailed):
        return state, ()
    panel = replace(state.panel, facts=event.facts)
    return on_key(
        state=replace(state, panel=panel),
        event=ApiKeyChanged(present=event.facts.key_set),
    )


def device_status(*, state: State) -> DeviceStatus:
    """
    Gather what the device reports about itself.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    DeviceStatus
        The timetable, rebuild, calls today, key and latest facts.
    """
    return DeviceStatus(
        timetable=state.saving.timetable,
        schema=state.rebuild.schema,
        rebuilding=state.rebuild.running,
        rebuild_failed=state.rebuild.failures > 0,
        calls_today=calls_today(quota=state.quota, day=state.now.date()),
        key_set=state.panel.key_set,
        facts=state.panel.facts,
    )
