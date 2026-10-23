"""Polling the live feeds, and waking the screen to poll at once."""

from dataclasses import replace
from datetime import datetime

from paperroute.core.chooser_steps import trip_feeds
from paperroute.core.menu import on_asked
from paperroute.core.messages import (
    ConfirmAsked,
    Effect,
    FetchFeed,
    KnobEvent,
    KnobsHeld,
)
from paperroute.core.rhythm import poll_every
from paperroute.core.sleep import awake
from paperroute.core.state_model import State, elapsed
from paperroute.core.state_views import settled


def poll_due(*, state: State, now: datetime) -> bool:
    """
    Say whether the trip-updates feeds should be fetched again.

    Positions feeds keep their own clocks in `MapState.fetched_at`.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when there are feeds to poll and the poll interval of real
        time has passed since the last fetch.
    """
    return bool(trip_feeds(state=state)) and elapsed(
        since=state.last_fetch,
        now=now,
        span=poll_every(state=state),
    )



def poll_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Fetch the trip-updates feeds when a poll is due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the fetch time recorded, and a FetchFeed for each feed;
        unchanged when no poll is due.
    """
    if not poll_due(state=state, now=state.now):
        return state, ()
    fetches = tuple(
        FetchFeed(feed=feed) for feed in trip_feeds(state=state)
    )
    return replace(state, last_fetch=state.now), fetches



def woken(
    *, state: State, event: KnobEvent | KnobsHeld | ConfirmAsked
) -> tuple[State, tuple[Effect, ...]]:
    """
    Wake the screen, polling the feeds at once.

    The touch that wakes is not acted on. A phone's ask is the
    exception: it wakes the screen and shows its confirm, in one redraw.

    Parameters
    ----------
    state : State
        State, asleep.
    event : KnobEvent | KnobsHeld | ConfirmAsked
        What woke it.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State awake and the fetches, with the Live view drawn, or the
        confirm when the event was an ask the panel shows.
    """
    state, polls = poll_effects(state=awake(state=state))
    if isinstance(event, ConfirmAsked):
        asked, effects = on_asked(state=state, event=event)
        if effects:
            return asked, (*polls, *effects)
    return settled(state=state, effects=polls)
