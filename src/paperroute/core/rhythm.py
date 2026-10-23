"""How often to poll: the peak and off-peak rates and the quota guard."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Final

from paperroute.core.messages import HolidaysLoaded
from paperroute.core.quota import DAILY_LIMIT, calls_today
from paperroute.core.saved import Rate, Settings, Window
from paperroute.core.state_model import State

RATES: Final[dict[Rate, tuple[timedelta, timedelta]]] = {
    Rate.FAST: (timedelta(seconds=30), timedelta(minutes=5)),
    Rate.STEADY: (timedelta(seconds=60), timedelta(minutes=10)),
    Rate.SLOW: (timedelta(minutes=2), timedelta(minutes=15)),
}
GUARDS: Final[tuple[tuple[float, timedelta], ...]] = (
    (0.95, timedelta(minutes=5)),
    (0.8, timedelta(seconds=60)),
)
FRIDAY: Final[int] = 4


def in_window(*, moment: time, window: Window) -> bool:
    """
    Say whether a time of day falls inside a window.

    Parameters
    ----------
    moment : time
        The time of day.
    window : Window
        The window; it wraps midnight when it ends before it starts.

    Returns
    -------
    bool
        True from the start up to the end; never for a window whose start
        and end are the same.
    """
    if window.start <= window.end:
        return window.start <= moment < window.end
    return moment >= window.start or moment < window.end


def is_peak(
    *, now: datetime, settings: Settings, holidays: frozenset[date]
) -> bool:
    """
    Say whether a moment is in peak hours.

    Parameters
    ----------
    now : datetime
        The moment, in Sydney time.
    settings : Settings
        The peak windows.
    holidays : frozenset[date]
        The public holidays, which are off-peak.

    Returns
    -------
    bool
        True on a weekday that isn't a holiday, inside a peak window.
    """
    if now.weekday() > FRIDAY or now.date() in holidays:
        return False
    moment = now.time()
    return any(
        in_window(moment=moment, window=window)
        for window in settings.peak_windows
    )


def rate_now(*, state: State) -> Rate:
    """
    Give the rate chosen for the current time.

    Parameters
    ----------
    state : State
        State; its time, settings and holidays decide.

    Returns
    -------
    Rate
        The peak rate in peak hours, otherwise the off-peak rate.
    """
    peak = is_peak(
        now=state.now,
        settings=state.settings,
        holidays=state.calendar.holidays,
    )
    return state.settings.peak_rate if peak else state.settings.offpeak_rate


def poll_every(*, state: State) -> timedelta:
    """
    Give the time between polls of a feed.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    timedelta
        The rate's poll interval, raised to the floor of the first guard
        whose share of `DAILY_LIMIT` today's calls are over.
    """
    every, _ = RATES[rate_now(state=state)]
    calls = calls_today(quota=state.quota, day=state.now.date())
    floor = next(
        (
            floor
            for fraction, floor in GUARDS
            if calls > fraction * DAILY_LIMIT
        ),
        every,
    )
    return max(every, floor)


def full_every(*, state: State) -> timedelta:
    """
    Give the time between full refreshes of the panel.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    timedelta
        The rate's full-refresh interval.
    """
    _, every = RATES[rate_now(state=state)]
    return every


def holidays_loaded(*, state: State, event: HolidaysLoaded) -> State:
    """
    Keep the public holidays read from the timetable.

    Parameters
    ----------
    state : State
        State.
    event : HolidaysLoaded
        The holidays.

    Returns
    -------
    State
        State with the holidays in its calendar.
    """
    calendar = replace(state.calendar, holidays=event.dates)
    return replace(state, calendar=calendar)
