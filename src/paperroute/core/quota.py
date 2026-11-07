"""Counting the API calls made each Sydney day."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Final

from paperroute.core.messages import (
    Effect,
    Event,
    FeedFailed,
    FeedJoined,
    FetchFeed,
)
from paperroute.core.saved import Quota, Saved
from paperroute.core.state_model import State

QUOTA_SAVE_EVERY: Final[timedelta] = timedelta(minutes=10)


def counted(*, quota: Quota, day: date, calls: int) -> Quota:
    """
    Add calls made on a day.

    Parameters
    ----------
    quota : Quota
        The calls counted so far.
    day : date
        The Sydney day of the new calls.
    calls : int
        The number of new calls.

    Returns
    -------
    Quota
        The count for `day`; it starts again at `calls` when `quota` is
        for another day.
    """
    before = quota.calls if quota.day == day else 0
    return Quota(day=day, calls=before + calls)


def calls_today(*, quota: Quota, day: date) -> int:
    """
    Give the calls made on a day.

    Parameters
    ----------
    quota : Quota
        The calls counted.
    day : date
        The Sydney day.

    Returns
    -------
    int
        The calls counted for `day`; 0 when `quota` is for another day.
    """
    return quota.calls if quota.day == day else 0


def with_calls(*, state: State, effects: tuple[Effect, ...]) -> State:
    """
    Count the feed fetches among some effects.

    Parameters
    ----------
    state : State
        State; its time gives the day.
    effects : tuple[Effect, ...]
        The effects a step emitted.

    Returns
    -------
    State
        State with each FetchFeed counted as one call; the same state when
        there are none, so the day only moves on when a call is made.
    """
    fetches = sum(isinstance(effect, FetchFeed) for effect in effects)
    if not fetches:
        return state
    quota = counted(quota=state.quota, day=state.now.date(), calls=fetches)
    return replace(state, quota=quota)


def without_skipped(*, state: State, event: Event) -> State:
    """
    Give back the call of a fetch that sent no request.

    A fetch is counted when it is asked for; one turned away by its
    feed's backoff, or one that shared another fetch's request, never
    reached TfNSW.

    Parameters
    ----------
    state : State
        State; its time gives the day.
    event : Event
        What happened.

    Returns
    -------
    State
        State with one call fewer today for a FeedFailed that was not
        sent or a FeedJoined; the same state otherwise, or when today
        has no calls.
    """
    unsent = isinstance(event, FeedFailed) and not event.sent
    if not (unsent or isinstance(event, FeedJoined)):
        return state
    if calls_today(quota=state.quota, day=state.now.date()) == 0:
        return state
    quota = replace(state.quota, calls=state.quota.calls - 1)
    return replace(state, quota=quota)


def quota_only(*, before: Saved, after: Saved) -> bool:
    """
    Say whether two saved states differ in their quota alone.

    Parameters
    ----------
    before : Saved
        The earlier state.
    after : Saved
        The later state.

    Returns
    -------
    bool
        True when the quotas differ and nothing else does.
    """
    return before.quota != after.quota and before == replace(
        after,
        quota=before.quota,
    )
