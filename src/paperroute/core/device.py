"""Handlers for the device's own settings."""

from dataclasses import replace

from paperroute.core.messages import Effect, SettingsChanged
from paperroute.core.saved import Settings
from paperroute.core.state_model import State


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
