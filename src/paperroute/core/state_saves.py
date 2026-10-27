"""Writing the saved state: when a change is due to be saved."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.messages import Effect, SaveState
from paperroute.core.quota import QUOTA_SAVE_EVERY, quota_only
from paperroute.core.saved import Saved
from paperroute.core.state_model import State, elapsed
from paperroute.core.state_views import saved_of

SAVE_AFTER: Final[timedelta] = timedelta(seconds=3)


def save_waits(*, state: State, current: Saved) -> bool:
    """
    Say whether a change to the saved state is too new to write.

    Parameters
    ----------
    state : State
        State.
    current : Saved
        The saved state, which differs from what was last written.

    Returns
    -------
    bool
        True when something was written before and the change is younger
        than `SAVE_AFTER`, or a change in the quota alone is younger than
        `QUOTA_SAVE_EVERY`.
    """
    saving = state.saving
    if saving.saved is None:
        return False
    if quota_only(before=saving.saved, after=current):
        return not elapsed(
            since=saving.quota_at,
            now=state.now,
            span=QUOTA_SAVE_EVERY,
        )
    return not elapsed(
        since=saving.changed_at,
        now=state.now,
        span=SAVE_AFTER,
    )


def saved_effects(
    *, state: State, wait: bool = True
) -> tuple[State, tuple[Effect, ...]]:
    """
    Save when the saved state differs from what was last written.

    Parameters
    ----------
    state : State
        State.
    wait : bool
        Hold the save until `SAVE_AFTER` after the last change, or a
        quota-only change until `QUOTA_SAVE_EVERY` after the last quota
        was written, unless nothing was ever written.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the save recorded, and a SaveState; unchanged when
        nothing is due.
    """
    saving = state.saving
    current = saved_of(state=state)
    if current == saving.saved:
        return state, ()
    if wait and save_waits(state=state, current=current):
        return state, ()
    quota_at = saving.quota_at
    if saving.saved is None or saving.saved.quota != current.quota:
        quota_at = state.now
    saving = replace(saving, saved=current, quota_at=quota_at)
    return replace(state, saving=saving), (SaveState(saved=current),)
