"""When to rebuild an outdated timetable, and how long to wait after."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.banners import offline
from paperroute.core.messages import Effect, RebuildTimetable, TimetableSeen
from paperroute.core.state_model import Rebuild, State
from paperroute_device.core.panel_views import NoticeView
from paperroute_device.core.text import DOT, clock_text
from paperroute_transit.core.city import City
from paperroute_transit.core.model import TIMETABLE_SCHEMA

BUILDING: Final[str] = 'Building the timetable'
SETTINGS_HINT: Final[str] = 'Both knobs: hold to open Settings'
WAITING_WIFI: Final[str] = (
    f'Waiting for Wi-Fi{DOT}hold both knobs, then Wi-Fi'
)
REBUILD_OFF: Final[str] = (
    f'Rebuild is off{DOT}hold both knobs, then Rebuild timetable now'
)
WAITING_KEY: Final[str] = (
    f'Waiting for the API key{DOT}set it on your phone'
)
BACKOFF_FIRST: Final[timedelta] = timedelta(hours=1)
BACKOFF_MAX: Final[timedelta] = timedelta(hours=24)


def is_outdated(*, schema: str | None, origin: str | None, city: City) -> bool:
    """
    Say whether a timetable is older than the app's or for another place.

    Parameters
    ----------
    schema : str | None
        The file's schema version.
    origin : str | None
        The projection origin text the file records.
    city : City
        The city the app runs for.

    Returns
    -------
    bool
        True for a whole-number schema below `TIMETABLE_SCHEMA`, or for a
        recorded origin other than the city's. A newer, missing or
        unreadable schema, or a missing origin, is not outdated by itself.
    """
    if origin is not None and origin != city.projection.origin_text:
        return True
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
        True when outdated or missing, not running and past any wait.
    """
    waiting = rebuild.retry_at is not None and now < rebuild.retry_at
    wanted = rebuild.outdated or rebuild.missing
    return wanted and not rebuild.running and not waiting


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

    A failed rebuild asked for by hand leaves the count and wait alone.

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
    was_forced = rebuild.forced
    rebuild = replace(rebuild, running=False, forced=False, failed=not ok)
    if was_forced and not ok:
        return rebuild
    failures = 0 if ok else rebuild.failures + 1
    wait = min(BACKOFF_FIRST * 2 ** max(failures - 1, 0), BACKOFF_MAX)
    return replace(rebuild, failures=failures, retry_at=now + wait)


def held(*, state: State) -> bool:
    """
    Say whether an automatic rebuild must wait.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True with the setting off, while offline or with no API key.
    """
    no_key = state.panel.key_set is False
    switched_off = not state.settings.device.auto_rebuild
    return switched_off or no_key or offline(state=state)


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
        when one is due and nothing holds it; otherwise the state
        unchanged and no effects.
    """
    if held(state=state) or not due(rebuild=state.rebuild, now=state.now):
        return state, ()
    rebuild = started(rebuild=state.rebuild)
    return replace(state, rebuild=rebuild), (RebuildTimetable(),)


def forced(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Start a rebuild now, whatever the version, wait or setting.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the rebuild marked running and a forced
        RebuildTimetable; unchanged with no effects while one runs.
    """
    if state.rebuild.running:
        return state, ()
    rebuild = replace(started(rebuild=state.rebuild), forced=True)
    return replace(state, rebuild=rebuild), (RebuildTimetable(force=True),)


def seen_schema(*, state: State, seen: TimetableSeen) -> State:
    """
    Record whether the timetable seen is outdated or missing.

    Parameters
    ----------
    state : State
        State.
    seen : TimetableSeen
        The file's version and schema.

    Returns
    -------
    State
        State with the schema and origin noted and `rebuild.outdated` and
        `rebuild.missing` set from it.
    """
    rebuild = replace(
        state.rebuild,
        outdated=is_outdated(
            schema=seen.schema,
            origin=seen.origin,
            city=state.city,
        ),
        schema=seen.schema,
        origin=seen.origin,
        missing=seen.version is None,
    )
    return replace(state, rebuild=rebuild)


def building_detail(*, state: State) -> str:
    """
    Say why the timetable is not built yet, first reason first.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str
        How to reach Settings while it builds; else what it waits for:
        Wi-Fi, an API key, a rebuild by hand with the switch off, or the
        retry after a failure; else how to reach Settings.
    """
    rebuild = state.rebuild
    if rebuild.running:
        return SETTINGS_HINT
    if offline(state=state):
        return WAITING_WIFI
    if state.panel.key_set is False:
        return WAITING_KEY
    if not state.settings.device.auto_rebuild:
        return REBUILD_OFF
    retry_at = rebuild.retry_at
    if rebuild.failures and retry_at is not None and state.now < retry_at:
        retry = clock_text(moment=retry_at, zone=state.city.zone)
        return f'Last try failed{DOT}trying again at {retry}'
    return SETTINGS_HINT


def building_view(*, state: State) -> NoticeView:
    """
    Build the screen shown while there is no timetable.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    NoticeView
        The title, and the line from `building_detail`.
    """
    return NoticeView(title=BUILDING, detail=building_detail(state=state))


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
