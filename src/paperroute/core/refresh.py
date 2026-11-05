"""Which views draw grey, and when the knobs were touched."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.map_view import MapView
from paperroute.core.messages import Event, KnobEvent, KnobsHeld, ScreenView
from paperroute.core.state_model import Refresh, State, elapsed
from paperroute.core.strip_model import StripMapView

__all__ = [
    'Refresh',
    'needs_greys',
    'QUICK_FOR',
    'noted',
    'quick_now',
]

QUICK_FOR: Final[timedelta] = timedelta(seconds=3)


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


def noted(*, state: State, event: Event) -> State:
    """
    Note the first event and a knob touch.

    Parameters
    ----------
    state : State
        State after the event.
    event : Event
        What happened.

    Returns
    -------
    State
        State with `started_at` at now after the first event and
        `touched_at` at now after a knob event.
    """
    refresh = state.refresh
    if refresh.started_at is None:
        refresh = replace(refresh, started_at=state.now)
    if isinstance(event, KnobEvent | KnobsHeld):
        refresh = replace(refresh, touched_at=state.now)
    if refresh == state.refresh:
        return state
    return replace(state, refresh=refresh)


def quick_now(*, state: State) -> bool:
    """
    Say whether a knob was turned within the last `QUICK_FOR`.

    Parameters
    ----------
    state : State
        State at the event's time.

    Returns
    -------
    bool
        True when the knobs were touched and `QUICK_FOR` has not passed.
    """
    touched_at = state.refresh.touched_at
    return touched_at is not None and not elapsed(
        since=touched_at, now=state.now, span=QUICK_FOR,
    )
