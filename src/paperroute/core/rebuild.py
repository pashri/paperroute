"""When to rebuild an outdated timetable, and how long to wait after."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.messages import Effect, RebuildTimetable
from paperroute.core.model import TIMETABLE_SCHEMA
from paperroute.core.state_model import Rebuild, State

BACKOFF_FIRST: Final[timedelta] = timedelta(hours=1)
BACKOFF_MAX: Final[timedelta] = timedelta(hours=24)


def is_outdated(*, schema: str | None) -> bool:
    """
    Say whether a timetable's schema is older than the app's.

    Parameters
    ----------
    schema : str | None
        The file's schema version.

    Returns
    -------
    bool
        True only for a whole number below `TIMETABLE_SCHEMA`; a newer,
        missing or unreadable schema is never outdated.
    """
    if schema is None or not schema.isdigit():
        return False
    return int(schema) < int(TIMETABLE_SCHEMA)


def due(*, rebuild: Rebuild, now: datetime) -> bool:
    """
    Say whether a rebuild should start now.

    Parameters
    ----------
    rebuild : Rebuild
        Rebuild state.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when outdated, not running and past any wait.
    """
    waiting = rebuild.retry_at is not None and now < rebuild.retry_at
    return rebuild.outdated and not rebuild.running and not waiting


def started(*, rebuild: Rebuild) -> Rebuild:
    """
    Mark a rebuild as running.

    Parameters
    ----------
    rebuild : Rebuild
        Rebuild state.

    Returns
    -------
    Rebuild
        The state with `running` set.
    """
    return replace(rebuild, running=True)


def finished(*, rebuild: Rebuild, ok: bool, now: datetime) -> Rebuild:
    """
    Record a finished rebuild and when the next may start.

    Parameters
    ----------
    rebuild : Rebuild
        Rebuild state.
    ok : bool
        True when the rebuild succeeded.
    now : datetime
        Current time.

    Returns
    -------
    Rebuild
        Not running; the failure count reset on success or counted up,
        and a wait of `BACKOFF_FIRST`, doubled for each failure in a row
        up to `BACKOFF_MAX`.
    """
    failures = 0 if ok else rebuild.failures + 1
    wait = min(BACKOFF_FIRST * 2 ** max(failures - 1, 0), BACKOFF_MAX)
    return replace(
        rebuild, running=False, failures=failures, retry_at=now + wait,
    )


def rebuild_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Start a rebuild when one is due.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the rebuild marked running and a RebuildTimetable
        when one is due and the setting allows it; otherwise the state
        unchanged and no effects.
    """
    if not state.settings.auto_rebuild:
        return state, ()
    if not due(rebuild=state.rebuild, now=state.now):
        return state, ()
    rebuild = started(rebuild=state.rebuild)
    return replace(state, rebuild=rebuild), (RebuildTimetable(),)


def seen_schema(*, state: State, schema: str | None) -> State:
    """
    Record whether the timetable seen is outdated.

    Parameters
    ----------
    state : State
        State.
    schema : str | None
        The file's schema version.

    Returns
    -------
    State
        State with `rebuild.outdated` set from the schema.
    """
    outdated = is_outdated(schema=schema)
    return replace(state, rebuild=replace(state.rebuild, outdated=outdated))


def rebuilt(*, state: State, ok: bool) -> State:
    """
    Record a finished rebuild.

    Parameters
    ----------
    state : State
        State.
    ok : bool
        True when it succeeded.

    Returns
    -------
    State
        State with the rebuild finished at the state's time.
    """
    rebuild = finished(rebuild=state.rebuild, ok=ok, now=state.now)
    return replace(state, rebuild=rebuild)
