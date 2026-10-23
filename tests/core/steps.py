"""Stepping helpers for reducer tests."""

from dataclasses import replace
from datetime import time

from paperroute.core.messages import Effect, Event
from paperroute.core.saved import Window
from paperroute.core.state import step
from paperroute.core.state_model import State


def wakeful(*, state: State) -> State:
    """
    Set quiet hours that never come.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State whose My hours start and end together.
    """
    never = Window(start=time(0), end=time(0))
    return replace(state, settings=replace(state.settings, my_hours=never))


def untouched(*, state: State) -> State:
    """
    Blank the time of the last knob touch.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with no touch noted.
    """
    return replace(state, refresh=replace(state.refresh, touched_at=None))


def step_untouched(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply an event, blanking the touch it notes.

    Parameters
    ----------
    state : State
        State before, with no touch noted.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after, with no touch noted, and effects.
    """
    after, effects = step(state, event)
    return untouched(state=after), effects
