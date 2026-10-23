"""Tests for the peak and off-peak rates and the quota guard."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from paperroute.core.model import SYDNEY
from paperroute.core.rhythm import full_every, in_window, is_peak, poll_every
from paperroute.core.saved import Quota, Rate, Settings, Window, seeded
from paperroute.core.state import initial_state
from paperroute.core.state_model import Calendar, State
from tests.core.factories import pin_of

MONDAY = date(2026, 10, 19)
TUESDAY = date(2026, 10, 20)
AUSTRALIA_DAY = date(2026, 1, 26)


def at(*, day: date = TUESDAY, hour: int, minute: int = 0) -> datetime:
    """
    Give a Sydney moment.

    Parameters
    ----------
    day : date
        The day.
    hour : int
        The hour.
    minute : int
        The minute.

    Returns
    -------
    datetime
        The moment in Sydney time.
    """
    return datetime.combine(day, time(hour, minute), tzinfo=SYDNEY)


def state_at(
    *,
    now: datetime,
    settings: Settings | None = None,
    quota: Quota | None = None,
) -> State:
    """
    Make a state at a moment with some settings and calls counted.

    Parameters
    ----------
    now : datetime
        The moment.
    settings : Settings | None
        The settings; the defaults when None.
    quota : Quota | None
        The calls counted; none when None.

    Returns
    -------
    State
        The state.
    """
    state = initial_state(
        saved=seeded(pin=pin_of('A', None)), now=now, stored=True
    )
    return replace(
        state,
        settings=settings or Settings(),
        quota=quota or Quota(),
    )


@pytest.mark.parametrize(
    ('hour', 'minute', 'peak'),
    [
        (6, 29, False),
        (6, 30, True),
        (9, 59, True),
        (10, 0, False),
        (15, 0, True),
        (19, 0, False),
    ],
)
def test_peak_windows(hour: int, minute: int, peak: bool) -> None:
    """The default windows are 06:30-10:00 and 15:00-19:00 on weekdays."""
    now = at(hour=hour, minute=minute)
    assert is_peak(now=now, settings=Settings(), holidays=frozenset()) is peak


def test_weekend_off_peak() -> None:
    """A Saturday morning is off-peak."""
    now = at(day=date(2026, 10, 24), hour=8)
    assert not is_peak(now=now, settings=Settings(), holidays=frozenset())


def test_holiday_off_peak() -> None:
    """A public holiday on a Monday is off-peak."""
    now = at(day=AUSTRALIA_DAY, hour=8)
    holidays = frozenset({AUSTRALIA_DAY})
    assert not is_peak(now=now, settings=Settings(), holidays=holidays)
    assert is_peak(now=now, settings=Settings(), holidays=frozenset())


def test_no_windows_never_peak() -> None:
    """With no peak windows it is never peak."""
    settings = Settings(peak_windows=())
    assert not is_peak(now=at(hour=8), settings=settings, holidays=frozenset())


def test_in_window_wraps_midnight() -> None:
    """A window ending before it starts wraps; one with no length never."""
    late = Window(start=time(23), end=time(5, 30))
    assert in_window(moment=time(1), window=late)
    assert not in_window(moment=time(12), window=late)
    empty = Window(start=time(8), end=time(8))
    assert not in_window(moment=time(8), window=empty)


def test_poll_every_follows_rate() -> None:
    """The peak and off-peak rates give the poll interval."""
    peak = state_at(now=at(hour=8))
    offpeak = state_at(now=at(hour=12))
    steady = replace(offpeak, settings=Settings(offpeak_rate=Rate.STEADY))
    assert poll_every(state=peak) == timedelta(seconds=30)
    assert poll_every(state=offpeak) == timedelta(minutes=2)
    assert poll_every(state=steady) == timedelta(seconds=60)


@pytest.mark.parametrize(
    ('calls', 'rate', 'day', 'every'),
    [
        (48_001, Rate.FAST, TUESDAY, timedelta(seconds=60)),
        (48_001, Rate.SLOW, TUESDAY, timedelta(minutes=2)),
        (57_001, Rate.FAST, TUESDAY, timedelta(minutes=5)),
        (48_000, Rate.FAST, TUESDAY, timedelta(seconds=30)),
        (57_001, Rate.FAST, MONDAY, timedelta(seconds=30)),
    ],
)
def test_guard_only_slows(
    calls: int, rate: Rate, day: date, every: timedelta
) -> None:
    """Over 80% or 95% of the day's calls slows polling, never speeds it."""
    state = state_at(
        now=at(hour=8),
        settings=Settings(peak_rate=rate),
        quota=Quota(day=day, calls=calls),
    )
    assert poll_every(state=state) == every


@pytest.mark.parametrize(
    ('rate', 'every'),
    [
        (Rate.FAST, timedelta(minutes=5)),
        (Rate.STEADY, timedelta(minutes=10)),
        (Rate.SLOW, timedelta(minutes=15)),
    ],
)
def test_full_every_follows_rate(rate: Rate, every: timedelta) -> None:
    """The full-refresh interval follows the rate, whatever the quota."""
    state = state_at(
        now=at(hour=8),
        settings=Settings(peak_rate=rate),
        quota=Quota(day=TUESDAY, calls=59_000),
    )
    assert full_every(state=state) == every


def test_poll_every_on_a_holiday_uses_offpeak() -> None:
    """The holidays the state knows make a weekday off-peak."""
    state = state_at(now=at(day=AUSTRALIA_DAY, hour=8))
    calendar = Calendar(holidays=frozenset({AUSTRALIA_DAY}))
    state = replace(state, calendar=calendar)
    assert poll_every(state=state) == timedelta(minutes=2)
