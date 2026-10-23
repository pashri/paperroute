"""Stepping helpers for reducer tests."""

from dataclasses import replace

from paperroute.core.messages import Effect, Event
from paperroute.core.state import step
from paperroute.core.state_model import State


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
