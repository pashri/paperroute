"""Following the timetable: when the shown pin's service is over."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Final

from paperroute_device.core.device_settings import QuietMode
from paperroute_transit.core.messages import (
    Effect,
    LoadServiceSpan,
    ServiceSpanLoaded,
)
from paperroute_transit.core.model import same_place
from paperroute_transit.core.places import shown
from paperroute_transit.core.state_model import Calendar, State

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
    if state.settings.device.quiet_mode is not QuietMode.APP:
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
