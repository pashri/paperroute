"""When the panel gets a full refresh."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.map_view import MapView
from paperroute.core.messages import (
    Effect,
    Event,
    KnobEvent,
    KnobsHeld,
    Render,
    ScreenView,
)
from paperroute.core.rhythm import full_every
from paperroute.core.state_model import Refresh, State, elapsed
from paperroute.core.strip_model import StripMapView

__all__ = [
    'IDLE_FOR',
    'LATE_BY',
    'Refresh',
    'full_due',
    'full_refresh_effects',
    'needs_greys',
    'noted',
    'with_full',
]

IDLE_FOR: Final[timedelta] = timedelta(seconds=20)
LATE_BY: Final[timedelta] = timedelta(minutes=2)


def needs_greys(*, view: ScreenView | None) -> bool:
    """
    Say whether a view draws grey tiles.

    Parameters
    ----------
    view : ScreenView | None
        The view, or None for none drawn.

    Returns
    -------
    bool
        True for the Map and Strip + map views.
    """
    return isinstance(view, MapView | StripMapView)


def full_due(*, state: State) -> bool:
    """
    Say whether a scheduled full refresh is due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    bool
        True when a view is drawn, the panel is awake and not powering
        off or resetting, the last full refresh is at least the rate's
        interval ago, and the knobs have been idle `IDLE_FOR` or the
        refresh is `LATE_BY` overdue.
    """
    if state.last_view is None or state.panel.asleep:
        return False
    if state.power is not None or state.panel.resetting is not None:
        return False
    every, refresh = full_every(state=state), state.refresh
    if not elapsed(since=refresh.last_full, now=state.now, span=every):
        return False
    return elapsed(
        since=refresh.touched_at, now=state.now, span=IDLE_FOR,
    ) or elapsed(
        since=refresh.last_full, now=state.now, span=every + LATE_BY,
    )


def full_refresh_effects(
    *, state: State
) -> tuple[State, tuple[Effect, ...]]:
    """
    Redraw the view shown with a full refresh when one is due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and a full Render of the last view when due, else no
        effects.
    """
    if not full_due(state=state):
        return state, ()
    assert state.last_view is not None
    return state, (Render(view=state.last_view, full=True),)


def with_full(
    *, state: State, effects: tuple[Effect, ...]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Add a due full refresh to a tick's effects.

    Parameters
    ----------
    state : State
        State after the tick.
    effects : tuple[Effect, ...]
        The tick's effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects; a due full Render replaces any other Render,
        which would draw the same view.
    """
    state, fulls = full_refresh_effects(state=state)
    if not fulls:
        return state, effects
    kept = tuple(
        effect for effect in effects if not isinstance(effect, Render)
    )
    return state, (*kept, *fulls)


def noted(
    *, state: State, event: Event, effects: tuple[Effect, ...]
) -> State:
    """
    Note a knob touch and a full refresh.

    Parameters
    ----------
    state : State
        State after the event.
    event : Event
        What happened.
    effects : tuple[Effect, ...]
        The event's effects.

    Returns
    -------
    State
        State with `touched_at` at now after a knob event and
        `last_full` at now after a full Render.
    """
    refresh = state.refresh
    if isinstance(event, KnobEvent | KnobsHeld):
        refresh = replace(refresh, touched_at=state.now)
    if any(
        isinstance(effect, Render) and effect.full for effect in effects
    ):
        refresh = replace(refresh, last_full=state.now)
    if refresh == state.refresh:
        return state
    return replace(state, refresh=refresh)
