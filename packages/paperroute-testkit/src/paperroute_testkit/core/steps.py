"""Stepping helpers for reducer tests."""

from dataclasses import replace
from datetime import time

from paperroute_device.core.device_settings import Window
from paperroute_transit.core.messages import (
    Effect,
    Event,
    FeedFailed,
    FetchFeed,
)
from paperroute_transit.core.state import step
from paperroute_transit.core.state_model import State


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
    device = replace(state.settings.device, my_hours=never)
    return replace(state, settings=replace(state.settings, device=device))


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


def answer_fetches(
    *, state: State, effects: tuple[Effect, ...]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Answer every feed fetched in some effects with a failure.

    A failure stores nothing, so the poll cycle's frame is released
    without changing what it shows.

    Parameters
    ----------
    state : State
        State after the effects were made.
    effects : tuple[Effect, ...]
        Effects, some of them FetchFeeds.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after the answers, and the effects followed by theirs.
    """
    fetches = [effect for effect in effects if isinstance(effect, FetchFeed)]
    for fetch in fetches:
        state, more = step(
            state,
            FeedFailed(feed=fetch.feed, at=state.now, reason='test'),
        )
        effects = (*effects, *more)
    return state, effects


def step_answered(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply an event, then answer every feed it fetched.

    Parameters
    ----------
    state : State
        State before.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after the answers, and every effect in order.
    """
    after, effects = step(state, event)
    return answer_fetches(state=after, effects=effects)
