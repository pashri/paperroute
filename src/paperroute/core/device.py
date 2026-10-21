"""Handlers for the device's own settings and power."""

from dataclasses import replace
from typing import Final

from paperroute.core.messages import (
    ControlPower,
    Effect,
    NoticeView,
    PowerAction,
    PowerFailed,
    PowerRequested,
    Render,
    SaveState,
    SettingsChanged,
)
from paperroute.core.saved import Settings
from paperroute.core.state_model import State
from paperroute.core.state_views import saved_of, settled

NOTICE: Final[dict[PowerAction, str]] = {
    PowerAction.OFF: 'Shut down - safe to unplug',
    PowerAction.RESTART: 'Restarting',
}


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
        is already under way.
    """
    if state.power is not None:
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


def on_device_event(
    *, state: State, event: SettingsChanged | PowerRequested | PowerFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store a setting, start a power action, or give up on a failed one.

    Parameters
    ----------
    state : State
        State.
    event : SettingsChanged | PowerRequested | PowerFailed
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    match event:
        case SettingsChanged():
            return on_settings(state=state, event=event)
        case PowerFailed():
            return on_power_failed(state=state, event=event)
    return on_power(state=state, event=event)


def power_guard(
    *, state: State, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Drop every Render from a result while a power action is under way.

    Parameters
    ----------
    state : State
        State before the event.
    result : tuple[State, tuple[Effect, ...]]
        The handler's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The result, without Renders when an action is under way, and with
        the notice kept as the view last drawn.
    """
    after, effects = result
    if state.power is None:
        return result
    kept = tuple(
        effect for effect in effects if not isinstance(effect, Render)
    )
    return replace(after, last_view=state.last_view), kept
