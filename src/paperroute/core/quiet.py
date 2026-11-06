"""Quiet hours: when the screen sleeps by itself, and when it wakes."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Final

from paperroute.core.auto_model import holds_awake
from paperroute.core.messages import (
    Effect,
    FetchFeed,
    LoadServiceSpan,
    Render,
    ServiceSpanLoaded,
)
from paperroute.core.model import same_place
from paperroute.core.places import shown
from paperroute.core.rhythm import in_window
from paperroute.core.saved import QuietMode
from paperroute.core.sleep import awake, slept
from paperroute.core.state_model import Calendar, State, elapsed
from paperroute.core.state_views import settled

MARGIN: Final[timedelta] = timedelta(minutes=30)
ONE_DAY: Final[timedelta] = timedelta(days=1)


def between_spans(*, state: State) -> bool:
    """
    Say whether the shown pin's service is over for the night.

    Parameters
    ----------
    state : State
        State, with its spans.

    Returns
    -------
    bool
        True when, for yesterday or today, that day's span and the
        next day's are both known and now is from `MARGIN` after the
        day's last trip ends to `MARGIN` before the next day's first
        trip starts.
    """
    spans, today = state.calendar.spans, state.now.date()
    for day in (today - ONE_DAY, today):
        last, first = spans.get(day), spans.get(day + ONE_DAY)
        if last is None or first is None:
            continue
        if last.last_end + MARGIN <= state.now < first.first_start - MARGIN:
            return True
    return False


def quiet_due(*, state: State) -> bool:
    """
    Say whether it is quiet hours now.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        By My hours, whether now is inside them; by the timetable,
        whether the pin's service is over for the night.
    """
    if state.settings.quiet_mode is QuietMode.MY_HOURS:
        return in_window(
            moment=state.now.time(), window=state.settings.my_hours,
        )
    return between_spans(state=state)


def may_sleep(*, state: State) -> bool:
    """
    Say whether quiet hours may put the screen to sleep now.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True when awake with no wake running, no confirm, menu, route
        chooser or Wi-Fi setup open, and no power action or reset under
        way.
    """
    panel = state.panel
    if panel.asleep or state.power is not None:
        return False
    if state.chooser is not None:
        return False
    if panel.awake_until is not None and panel.awake_until > state.now:
        return False
    return (panel.confirm, panel.menu, panel.wifi, panel.resetting) == (
        None, None, None, None,
    )


def fall_asleep(
    *, state: State, by_quiet: bool
) -> tuple[State, tuple[Effect, ...]]:
    """
    Put the screen to sleep and draw the sleep screen.

    Parameters
    ----------
    state : State
        State, awake.
    by_quiet : bool
        Whether quiet hours sleep it, so a tick can end the sleep.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State asleep with no wake running, and a full Render of the
        sleep screen.
    """
    return settled(state=slept(state=state, by_quiet=by_quiet))


def quiet_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Sleep at the start of quiet hours and wake at their end.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        A quiet sleep that is no longer due wakes, starting a cycle at
        once; quiet hours sleep a screen that `may_sleep`; a wake run out
        outside quiet hours is forgotten. A Sleep now sleep is left alone.
        Quiet hours are not due while an auto window holds the panel
        awake.
    """
    panel = state.panel
    due = quiet_due(state=state) and not holds_awake(auto=state.auto)
    if panel.asleep:
        if panel.by_quiet and not due:
            return settled(state=awake(state=state))
        return state, ()
    if due and may_sleep(state=state):
        return fall_asleep(state=state, by_quiet=True)
    if panel.awake_until is not None and not due and elapsed(
        since=panel.awake_until, now=state.now, span=timedelta(),
    ):
        return replace(state, panel=replace(panel, awake_until=None)), ()
    return state, ()


def with_quiet(
    *, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Add quiet hours' sleep or wake to a tick's result.

    Parameters
    ----------
    result : tuple[State, tuple[Effect, ...]]
        The tick's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The result; a sleep or wake drops the tick's Renders and
        FetchFeeds for its own, so the screen is drawn once and the
        feeds asked for once.
    """
    state, effects = result
    state, quiet = quiet_effects(state=state)
    if not quiet:
        return state, effects
    kept = tuple(
        effect
        for effect in effects
        if not isinstance(effect, Render | FetchFeed)
    )
    return state, (*kept, *quiet)


def service_days(*, state: State) -> tuple[date, ...]:
    """
    List the service days whose spans quiet hours need.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[date, ...]
        Yesterday, today and tomorrow.
    """
    today = state.now.date()
    return (today - ONE_DAY, today, today + ONE_DAY)


def spans_kept(*, state: State) -> Calendar:
    """
    Keep the spans still useful for the pin shown.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Calendar
        The calendar for the shown pin and timetable version, with no
        spans when either changed, and only the days `service_days`
        lists.
    """
    calendar, pin = state.calendar, shown(state=state)
    key = (pin, state.saving.timetable)
    if calendar.spans_for is None or calendar.spans_for[1] != key[1] or (
        not same_place(first=calendar.spans_for[0], second=pin)
    ):
        calendar = replace(
            calendar, spans={}, pending=frozenset(), spans_for=key,
        )
    days = service_days(state=state)
    return replace(
        calendar,
        spans={day: calendar.spans[day] for day in calendar.spans
               if day in days},
        pending=calendar.pending & frozenset(days),
    )


def span_requests(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the shown pin's spans when following the timetable.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the spans kept and the days asked for pending, and a
        LoadServiceSpan for each of yesterday, today and tomorrow neither
        loaded nor pending; unchanged by My hours.
    """
    if state.settings.quiet_mode is not QuietMode.TIMETABLE:
        return state, ()
    calendar = spans_kept(state=state)
    wanted = tuple(
        day
        for day in service_days(state=state)
        if day not in calendar.spans and day not in calendar.pending
    )
    loads = tuple(
        LoadServiceSpan(
            pin=shown(state=state), day=day, timetable=state.saving.timetable,
        )
        for day in wanted
    )
    calendar = replace(calendar, pending=calendar.pending | set(wanted))
    return replace(state, calendar=calendar), loads


def span_loaded(*, state: State, event: ServiceSpanLoaded) -> State:
    """
    Keep a span read from the timetable.

    Parameters
    ----------
    state : State
        State.
    event : ServiceSpanLoaded
        The span.

    Returns
    -------
    State
        State with the span kept and its day no longer pending.
    """
    calendar = state.calendar
    calendar = replace(
        calendar,
        spans={**calendar.spans, event.day: event.span},
        pending=calendar.pending - {event.day},
    )
    return replace(state, calendar=calendar)
