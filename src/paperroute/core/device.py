"""Handlers for the device's own settings and power."""

from dataclasses import fields, replace
from datetime import timedelta
from typing import Final

from paperroute.core.alert_state import alert_clocks_cleared
from paperroute.core.banners import feeds_held
from paperroute.core.device_model import (
    ConfirmAction,
    DeviceStatus,
    Origin,
    PanelScreen,
    ResetKind,
)
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
    RebuildTimetable,
    Render,
    ResetDevice,
    ResetDone,
    SaveState,
    SettingsChanged,
    Stop,
    WifiEvent,
)
from paperroute.core.quota import calls_today
from paperroute.core.state_model import State
from paperroute.core.state_views import saved_of, settled
from paperroute.core.wifi import on_wifi_event, setup_closed

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
WATCH_EVERY: Final[timedelta] = timedelta(seconds=5)


def on_settings(
    *, state: State, event: SettingsChanged
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store the settings given; the next tick saves them.

    A change to rotate redraws the last view with a full refresh; a
    change to the school switch drops the loaded departures, so the next
    tick reads them again.

    Parameters
    ----------
    state : State
        State.
    event : SettingsChanged
        The new settings; a None field is left as it is.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the settings stored, and a full Render of the last
        view when rotate changed and a view has been drawn.
    """
    given = {
        item.name: getattr(event, item.name)
        for item in fields(event)
        if getattr(event, item.name) is not None
    }
    settings = replace(state.settings, **given)
    turned = settings.rotate != state.settings.rotate
    after = replace(state, settings=settings)
    if settings.skip_school_buses != state.settings.skip_school_buses:
        after = replace(
            after,
            board=replace(after.board, loaded_until=None, loading=False),
        )
    if not turned or state.last_view is None:
        return after, ()
    return after, (Render(view=state.last_view, full=True),)


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
        notice and a ControlPower, in that order, after any Wi-Fi setup
        is stopped; nothing when an action or a reset is already under
        way.
    """
    if state.power is not None or state.panel.resetting is not None:
        return state, ()
    state, stops = setup_closed(state=state)
    saved = saved_of(state=state)
    view = NoticeView(title=NOTICE[event.action])
    state = replace(
        state,
        power=event.action,
        last_view=view,
        saving=replace(state.saving, saved=saved),
    )
    return state, (
        *stops,
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
        ResetDevice, in that order, after any Wi-Fi setup is stopped;
        nothing while a power action or a reset is under way.
    """
    if state.power is not None or state.panel.resetting is not None:
        return state, ()
    state, stops = setup_closed(state=state)
    view = NoticeView(title=RESET_NOTICE[kind], detail=RESET_DETAIL)
    panel = replace(state.panel, resetting=kind)
    state = replace(state, panel=panel, last_view=view)
    return state, (*stops, Render(view=view), ResetDevice(kind=kind))


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
    | WifiEvent
)


def on_device_event(
    *, state: State, event: DeviceEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store a setting, key state or facts; handle power or Wi-Fi setup.

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
        case DeviceProbed() | DeviceProbeFailed():
            result = on_probed(state=state, event=event)
        case _:
            after, effects = on_wifi_event(state=state, event=event)
            result = settled(state=after, effects=effects)
    return quiet_guard(result=power_guard(state=state, result=result))


def power_guard(
    *, state: State, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop Renders while a power action or reset is under way.

    While a reset is under way every SaveState and RebuildTimetable is
    dropped too, so the files it deletes are never written again.

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
    dropped: tuple[type[Effect], ...] = (Render,)
    if resetting:
        dropped = (Render, SaveState, RebuildTimetable)
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
        state after the event. Held feeds also have their poll clocks
        cleared, so they poll at once when they are let go.
    """
    after, effects = result
    if not feeds_held(state=after):
        return result
    kept = tuple(
        effect for effect in effects if not isinstance(effect, FetchFeed)
    )
    cleared = alert_clocks_cleared(
        state=replace(
            after,
            last_fetch=None,
            map=replace(after.map, fetched_at={}),
        ),
    )
    return cleared, kept


def probe_due(*, state: State) -> bool:
    """
    Say whether the device's facts should be asked for now.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True on the first tick, once `PROBE_EVERY` has passed, or once
        `WATCH_EVERY` has passed while the Device screen shows; never
        while a probe younger than `PROBE_EVERY` has not answered, nor
        while Wi-Fi setup is open.
    """
    panel = state.panel
    if panel.wifi is not None:
        return False
    if panel.probed_at is None:
        return True
    since = state.now - panel.probed_at
    if panel.probing and since < PROBE_EVERY:
        return False
    menu = panel.menu
    watched = menu is not None and menu.screen is PanelScreen.DEVICE
    return since >= (WATCH_EVERY if watched else PROBE_EVERY)


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
        State with the probe time noted and marked running, and a
        ProbeDevice, when `probe_due`; unchanged otherwise.
    """
    if not probe_due(state=state):
        return state, ()
    panel = replace(state.panel, probed_at=state.now, probing=True)
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
        only marked answered when the probe failed.
    """
    state = replace(state, panel=replace(state.panel, probing=False))
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
        The timetable, rebuild, calls today, key, latest facts, what
        a phone asked for that waits on the panel and Wi-Fi setup.
    """
    return DeviceStatus(
        timetable=state.saving.timetable,
        schema=state.rebuild.schema,
        rebuilding=state.rebuild.running,
        rebuild_failed=state.rebuild.failed,
        calls_today=calls_today(quota=state.quota, day=state.now.date()),
        key_set=state.panel.key_set,
        facts=state.panel.facts,
        asked=phone_asked(state=state),
        setup=state.panel.wifi,
    )


def phone_asked(*, state: State) -> ConfirmAction | None:
    """
    Say what a phone asked for that waits on the panel.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    ConfirmAction | None
        The action of a confirm a phone asked for, or None.
    """
    confirm = state.panel.confirm
    if confirm is None or confirm.origin is not Origin.PHONE:
        return None
    return confirm.action
