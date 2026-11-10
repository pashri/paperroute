"""The pure function that advances the kit's state, calling the app."""

from dataclasses import replace
from datetime import tzinfo
from typing import Final

from paperroute_device.core.app import App
from paperroute_device.core.device import (
    clock_checked,
    on_device_settings,
    on_key,
    on_power,
    on_power_failed,
    on_probed,
    on_reset_done,
    probe_effects,
    saved_effects,
)
from paperroute_device.core.device_events import (
    ApiKeyChanged,
    ConfirmAsked,
    JoinAsked,
    PowerRequested,
)
from paperroute_device.core.device_state import DeviceState
from paperroute_device.core.kit_effects import (
    Render,
    SaveState,
    Stop,
    is_kit_effect,
)
from paperroute_device.core.kit_events import (
    DeviceProbed,
    DeviceProbeFailed,
    HotspotFailed,
    HotspotStarted,
    PowerFailed,
    QuitRequested,
    ResetDone,
    SettingsPosted,
    Tick,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute_device.core.kit_state import (
    KitState,
    KitStep,
    with_app,
    with_device,
)
from paperroute_device.core.menu import (
    confirm_expired,
    confirm_knob,
    menu_idle,
    menu_knob,
    on_asked,
    opened,
    touched,
)
from paperroute_device.core.quiet import quiet_effects
from paperroute_device.core.shown import settled
from paperroute_device.core.sleep import awake, held_awake
from paperroute_device.core.wifi import (
    on_wifi_event,
    setup_expired,
    setup_knob,
)
from paperroute_hardware.core.knob_events import (
    KnobEvent,
    KnobHeld,
    KnobHeldTurned,
    KnobInput,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)

UNGUARDED: Final[tuple[type, ...]] = (
    PowerRequested,
    PowerFailed,
    SettingsPosted,
)

type Touch = KnobInput | ConfirmAsked


def step[S](
    *, kit: KitState[S], app: App[S], zone: tzinfo, event: object,
) -> KitStep[S]:
    """
    Apply one event: the kit's handlers first, the app for the rest.

    Parameters
    ----------
    kit : KitState[S]
        Current kit state.
    app : App[S]
        The app.
    zone : tzinfo
        Local time zone, for the panel screens' clock and the clock wait.
    event : object
        What happened.

    Returns
    -------
    KitStep[S]
        Next kit state and the effects to perform. `App.begin` runs
        first; then the handler, `App.on_device` when the device changed,
        the redraw, the power and reset guard, the noted times and
        `App.settle`, in that order.
    """
    begun = app.begin(state=kit.app, device=kit.device, event=event)
    start = with_app(kit=kit, app=begun)
    after, effects = dispatched(kit=start, app=app, zone=zone, event=event)
    after, effects = followed(
        before=kit, kit=after, app=app, effects=effects,
    )
    if after is not start or effects:
        after, effects = settled(
            kit=after, app=app, zone=zone, effects=effects,
        )
    after, effects = guarded(
        before=kit, kit=after, event=event, effects=effects,
    )
    after, effects = app_settled(
        before=kit,
        kit=noted(kit=after, event=event),
        app=app,
        event=event,
        effects=effects,
    )
    return changed(before=kit, after=after, app=app), effects


def dispatched[S](
    *, kit: KitState[S], app: App[S], zone: tzinfo, event: object,
) -> KitStep[S]:
    """
    Hand an event to its handler.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    zone : tzinfo
        Local time zone.
    event : object
        What happened.

    Returns
    -------
    KitStep[S]
        Next kit state and the handler's effects.
    """
    match event:
        case Tick():
            return on_ticked(kit=kit, app=app, zone=zone, tick=event)
        case (
            KnobTurned()
            | KnobPressed()
            | KnobHeld()
            | KnobHeldTurned()
            | KnobsHeld()
            | ConfirmAsked()
        ):
            return on_touch(kit=kit, app=app, event=event)
        case QuitRequested():
            return on_quit(kit=kit, app=app)
        case SettingsPosted():
            return on_posted(kit=kit, app=app, event=event)
        case PowerRequested():
            return on_power(kit=kit, app=app, event=event)
    return device_result(kit=kit, app=app, event=event)


def device_result[S](
    *, kit: KitState[S], app: App[S], event: object,
) -> KitStep[S]:
    """
    Handle a device event, or hand any other event to the app.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    event : object
        What happened.

    Returns
    -------
    KitStep[S]
        Next kit state and effects.
    """
    device = kit.device
    match event:
        case PowerFailed():
            device = on_power_failed(device=device, event=event)
        case ApiKeyChanged():
            device = on_key(device=device, event=event)
        case ResetDone():
            return on_reset_done(kit=kit)
        case DeviceProbed() | DeviceProbeFailed():
            device = on_probed(device=device, event=event)
        case (
            HotspotStarted()
            | HotspotFailed()
            | JoinAsked()
            | WifiJoined()
            | WifiJoinFailed()
            | WifiSetupStopped()
        ):
            device, effects = on_wifi_event(device=device, event=event)
            return with_device(kit=kit, device=device), effects
        case _:
            state, effects = app.on_event(
                state=kit.app, device=device, event=event,
            )
            return with_app(kit=kit, app=state), effects
    return with_device(kit=kit, device=device), ()


def on_ticked[S](
    *, kit: KitState[S], app: App[S], zone: tzinfo, tick: Tick,
) -> KitStep[S]:
    """
    Advance time: close what timed out, probe, tick the app and save.

    The app may then end a Sleep now sleep, and quiet hours sleep or
    wake the screen, before the save.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    zone : tzinfo
        Local time zone.
    tick : Tick
        Tick.

    Returns
    -------
    KitStep[S]
        New kit state; Wi-Fi setup's stop, the probe, the app's effects
        and the save, in that order.
    """
    device = clock_checked(
        device=kit.device, now=tick.now, synced=tick.synced, zone=zone,
    )
    device, stops = setup_expired(device=device)
    device = menu_idle(device=confirm_expired(device=device))
    device, probes = probe_effects(device=device)
    state, effects = app.on_tick(state=kit.app, device=device, tick=tick)
    device = app_woken(device=device, app=app, state=state)
    kit = with_app(kit=with_device(kit=kit, device=device), app=state)
    kit, quiet = quiet_effects(kit=kit, app=app)
    kit, saves = saved_effects(kit=kit, app=app)
    return kit, (*stops, *probes, *effects, *quiet, *saves)


def app_woken[S](
    *, device: DeviceState, app: App[S], state: S,
) -> DeviceState:
    """
    End a Sleep now sleep when the app asks.

    Parameters
    ----------
    device : DeviceState
        Device state at the tick's time.
    app : App[S]
        The app.
    state : S
        The app's state after the tick.

    Returns
    -------
    DeviceState
        Device state awake when it slept by hand and `App.wakes` says
        so; unchanged otherwise.
    """
    panel = device.panel
    if panel.asleep and not panel.by_quiet and app.wakes(state=state):
        return awake(device=device)
    return device


def on_touch[S](
    *, kit: KitState[S], app: App[S], event: Touch,
) -> KitStep[S]:
    """
    Hand a touch or a phone's ask to the confirm, menu or the app.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    event : Touch
        What happened.

    Returns
    -------
    KitStep[S]
        A phone's ask shows a confirm; a touch goes to `panel_touch`.
        While asleep the first touch only wakes the screen, for
        `WAKE_FOR`, and an ask wakes it and shows its confirm; a knob
        touch during a wake moves its end on. A touch during a power
        action or a reset does nothing.
    """
    device = kit.device
    if device.panel.asleep:
        device = awake(device=device)
        if isinstance(event, ConfirmAsked):
            device = on_asked(device=device, event=event)
        return with_device(kit=kit, device=held_awake(device=device)), ()
    if isinstance(event, ConfirmAsked):
        asked = on_asked(device=device, event=event)
        return with_device(kit=kit, device=asked), ()
    if device.power is not None or device.panel.resetting is not None:
        return kit, ()
    after, effects = panel_touch(kit=kit, app=app, event=event)
    if device.panel.awake_until is None:
        return after, effects
    held = held_awake(device=after.device)
    return with_device(kit=after, device=held), effects


def panel_touch[S](
    *, kit: KitState[S], app: App[S], event: KnobInput,
) -> KitStep[S]:
    """
    Hand a touch to the confirm, Wi-Fi setup, menu or the app.

    Parameters
    ----------
    kit : KitState[S]
        Kit state, awake.
    app : App[S]
        The app.
    event : KnobInput
        What happened.

    Returns
    -------
    KitStep[S]
        A touch answers the confirm shown, else works Wi-Fi setup, else
        works the open menu, else holding both knobs opens it, else it
        goes to `App.on_knob`. A touch while the menu is open is noted,
        so the menu stays open.
    """
    panel = kit.device.panel
    effects: tuple[object, ...] = ()
    if panel.confirm is not None:
        after, effects = confirm_knob(kit=kit, app=app, event=event)
    elif panel.wifi is not None:
        return setup_touch(kit=kit, event=event)
    elif panel.menu is not None:
        after, effects = menu_knob(kit=kit, app=app, event=event)
    elif isinstance(event, KnobsHeld):
        after = with_device(kit=kit, device=opened(device=kit.device))
    else:
        state, effects = app.on_knob(
            state=kit.app, device=kit.device, event=event,
        )
        return with_app(kit=kit, app=state), effects
    return with_device(kit=after, device=touched(device=after.device)), effects


def setup_touch[S](*, kit: KitState[S], event: KnobInput) -> KitStep[S]:
    """
    Hand a touch to Wi-Fi setup.

    Parameters
    ----------
    kit : KitState[S]
        Kit state with setup open.
    event : KnobInput
        What happened.

    Returns
    -------
    KitStep[S]
        The touch noted; `kit` itself when setup ignored it.
    """
    device, effects = setup_knob(device=kit.device, event=event)
    if device is kit.device:
        return kit, ()
    return with_device(kit=kit, device=touched(device=device)), effects


def on_quit[S](*, kit: KitState[S], app: App[S]) -> KitStep[S]:
    """
    Stop, saving any unsaved change first.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.

    Returns
    -------
    KitStep[S]
        Stopped kit state, and a SaveState if needed, then Stop.
    """
    kit, saves = saved_effects(kit=kit, app=app, wait=False)
    return replace(kit, running=False), (*saves, Stop())


def on_posted[S](
    *, kit: KitState[S], app: App[S], event: SettingsPosted,
) -> KitStep[S]:
    """
    Apply a Settings page post: the device part, then the app's.

    Parameters
    ----------
    kit : KitState[S]
        Kit state.
    app : App[S]
        The app.
    event : SettingsPosted
        The post.

    Returns
    -------
    KitStep[S]
        Kit state with the device settings stored and the app's part
        handed to `App.on_event` when there is one, and their effects.
    """
    device, effects = on_device_settings(
        device=kit.device, change=event.device,
    )
    kit = with_device(kit=kit, device=device)
    if event.app is None:
        return kit, effects
    state, more = app.on_event(state=kit.app, device=device, event=event.app)
    return with_app(kit=kit, app=state), (*effects, *more)


def followed[S](
    *,
    before: KitState[S],
    kit: KitState[S],
    app: App[S],
    effects: tuple[object, ...],
) -> KitStep[S]:
    """
    Tell the app when the event changed the device state.

    Parameters
    ----------
    before : KitState[S]
        Kit state before the event.
    kit : KitState[S]
        Kit state after the handler.
    app : App[S]
        The app.
    effects : tuple[object, ...]
        The handler's effects.

    Returns
    -------
    KitStep[S]
        Kit state with what `App.on_device` made of the change, and its
        effects after the handler's; unchanged when the device is.
    """
    if kit.device == before.device:
        return kit, effects
    state, more = app.on_device(
        state=kit.app, before=before.device, after=kit.device,
    )
    return with_app(kit=kit, app=state), (*effects, *more)


def guarded[S](
    *,
    before: KitState[S],
    kit: KitState[S],
    event: object,
    effects: tuple[object, ...],
) -> KitStep[S]:
    """
    Drop the kit's own redraws while a power action or reset is under way.

    While a reset is under way the kit's saves are dropped too, so the
    files it deletes are never written again. A power request, a power
    failure and a Settings post are not guarded.

    Parameters
    ----------
    before : KitState[S]
        Kit state before the event.
    kit : KitState[S]
        Kit state after the redraw.
    event : object
        What happened.
    effects : tuple[object, ...]
        The effects so far.

    Returns
    -------
    KitStep[S]
        The result, without the dropped effects when an action or reset
        is under way, and with the notice kept as the view last drawn.
    """
    device = before.device
    resetting = device.panel.resetting is not None
    busy = device.power is not None or resetting
    if isinstance(event, UNGUARDED) or not busy:
        return kit, effects
    dropped: tuple[type, ...] = (Render, SaveState) if resetting else (Render,)
    kept = tuple(
        effect for effect in effects if not isinstance(effect, dropped)
    )
    shown = replace(
        kit.device,
        last_view=device.last_view,
        last_was_sleep=device.last_was_sleep,
    )
    return with_device(kit=kit, device=shown), kept


def noted[S](*, kit: KitState[S], event: object) -> KitState[S]:
    """
    Note the first event and a knob touch.

    Parameters
    ----------
    kit : KitState[S]
        Kit state after the event.
    event : object
        What happened.

    Returns
    -------
    KitState[S]
        Kit state with `started_at` at now after the first event and
        `touched_at` at now after a knob event.
    """
    device = kit.device
    if device.started_at is None:
        device = replace(device, started_at=device.now)
    if isinstance(event, KnobEvent | KnobsHeld):
        device = replace(device, touched_at=device.now)
    return with_device(kit=kit, device=device)


def app_settled[S](
    *,
    before: KitState[S],
    kit: KitState[S],
    app: App[S],
    event: object,
    effects: tuple[object, ...],
) -> KitStep[S]:
    """
    Let the app wrap its part of the result.

    Parameters
    ----------
    before : KitState[S]
        Kit state before the event.
    kit : KitState[S]
        Kit state with the final device state.
    app : App[S]
        The app.
    event : object
        What happened.
    effects : tuple[object, ...]
        Every effect so far.

    Returns
    -------
    KitStep[S]
        Kit state with the app's settled state, and the kit's effects
        before the app's first one or the first Render, then the app's
        settled effects, then the rest of the kit's.
    """
    own = tuple(effect for effect in effects if not is_kit_effect(effect))
    state, settled_own = app.settle(
        before=before.app,
        after=kit.app,
        device=kit.device,
        event=event,
        effects=own,
    )
    split = next(
        (
            index
            for index, effect in enumerate(effects)
            if not is_kit_effect(effect) or isinstance(effect, Render)
        ),
        len(effects),
    )
    head = effects[:split]
    tail = tuple(
        effect for effect in effects[split:] if is_kit_effect(effect)
    )
    return with_app(kit=kit, app=state), (*head, *settled_own, *tail)


def changed[S](
    *, before: KitState[S], after: KitState[S], app: App[S],
) -> KitState[S]:
    """
    Note when what is saved changed.

    Parameters
    ----------
    before : KitState[S]
        Kit state before the event.
    after : KitState[S]
        Kit state after it.
    app : App[S]
        The app.

    Returns
    -------
    KitState[S]
        Kit state with `changed_at` at now when the device settings
        changed or `App.save_due` says the app's did; unchanged
        otherwise.
    """
    settings = before.device.settings != after.device.settings
    if settings or app.save_due(before=before.app, after=after.app):
        return replace(after, changed_at=after.device.now)
    return after
