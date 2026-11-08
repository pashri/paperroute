"""Tests for the peak and off-peak rates and the quota guard."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from paperroute_device.core.device_settings import Window
from paperroute_sydney.city import PEAK_WINDOWS, SYDNEY, SYDNEY_ZONE
from paperroute_testkit.core.factories import pin_of
from paperroute_transit.core.rhythm import (
    frame_time,
    in_window,
    is_peak,
    poll_every,
    stale_after,
)
from paperroute_transit.core.saved import (
    BoardSettings,
    Quota,
    Rate,
    Settings,
    default_settings,
    seeded,
)
from paperroute_transit.core.state import initial_state
from paperroute_transit.core.state_model import Calendar, Cycle, State

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
    return datetime.combine(day, time(hour, minute), tzinfo=SYDNEY_ZONE)


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
        saved=seeded(
            pin=pin_of('A', None),
            city=SYDNEY,
        ), now=now, stored=True, city=SYDNEY
    )
    return replace(
        state,
        settings=settings or default_settings(city=SYDNEY),
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
    assert is_peak(
        now=now,
        settings=default_settings(city=SYDNEY),
        holidays=frozenset(),
    ) is peak


def test_weekend_off_peak() -> None:
    """A Saturday morning is off-peak."""
    now = at(day=date(2026, 10, 24), hour=8)
    assert not is_peak(
        now=now,
        settings=default_settings(city=SYDNEY),
        holidays=frozenset(),
    )


def test_holiday_off_peak() -> None:
    """A public holiday on a Monday is off-peak."""
    now = at(day=AUSTRALIA_DAY, hour=8)
    holidays = frozenset({AUSTRALIA_DAY})
    assert not is_peak(
        now=now,
        settings=default_settings(city=SYDNEY),
        holidays=holidays,
    )
    assert is_peak(
        now=now,
        settings=default_settings(city=SYDNEY),
        holidays=frozenset(),
    )


def test_no_windows_never_peak() -> None:
    """With no peak windows it is never peak."""
    settings = Settings(board=BoardSettings(peak_windows=()))
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
    settings = Settings(
        board=BoardSettings(
            peak_windows=PEAK_WINDOWS,
            offpeak_rate=Rate.STEADY,
        ),
    )
    steady = replace(offpeak, settings=settings)
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
        settings=Settings(
            board=BoardSettings(peak_windows=PEAK_WINDOWS, peak_rate=rate),
        ),
        quota=Quota(day=day, calls=calls),
    )
    assert poll_every(state=state) == every


def test_poll_every_on_a_holiday_uses_offpeak() -> None:
    """The holidays the state knows make a weekday off-peak."""
    state = state_at(now=at(day=AUSTRALIA_DAY, hour=8))
    calendar = Calendar(holidays=frozenset({AUSTRALIA_DAY}))
    state = replace(state, calendar=calendar)
    assert poll_every(state=state) == timedelta(minutes=2)


def cycled(*, now: datetime, started: datetime | None) -> State:
    """
    Make a state at a moment whose last cycle started at another.

    Parameters
    ----------
    now : datetime
        The moment.
    started : datetime | None
        When the cycle started; None before the first.

    Returns
    -------
    State
        The state.
    """
    return replace(state_at(now=now), cycle=Cycle(started=started))


@pytest.mark.parametrize(
    ('started', 'frame'),
    [
        (None, at(hour=12, minute=3)),
        (at(hour=12, minute=2), at(hour=12, minute=3)),
        (
            at(hour=12, minute=3) + timedelta(seconds=4),
            at(hour=12, minute=3) + timedelta(seconds=4),
        ),
        (at(hour=12, minute=4), at(hour=12, minute=3)),
    ],
)
def test_frame_time_is_latest_cycle_start_or_minute(
    started: datetime | None, frame: datetime
) -> None:
    """A cycle started this minute wins; none, older or future, the minute."""
    now = at(hour=12, minute=3) + timedelta(seconds=41)
    assert frame_time(state=cycled(now=now, started=started)) == frame


def test_frame_time_holds_between_ticks() -> None:
    """Ticks inside one minute after the cycle share a frame time."""
    started = at(hour=12, minute=2) + timedelta(seconds=2)
    frames = {
        frame_time(
            state=cycled(now=started + timedelta(seconds=s), started=started),
        )
        for s in range(0, 58, 5)
    }
    assert frames == {started}


@pytest.mark.parametrize(
    ('hour', 'rate', 'calls', 'limit'),
    [
        (8, Rate.FAST, 0, timedelta(seconds=90)),
        (12, Rate.STEADY, 0, timedelta(minutes=2)),
        (12, Rate.SLOW, 0, timedelta(minutes=3)),
        (8, Rate.FAST, 57_001, timedelta(minutes=6)),
    ],
)
def test_stale_after_is_poll_interval_plus_a_minute(
    hour: int, rate: Rate, calls: int, limit: timedelta
) -> None:
    """Only a missed fetch makes data stale, at every rate and guard."""
    state = state_at(
        now=at(hour=hour),
        settings=Settings(
            board=BoardSettings(
                peak_windows=PEAK_WINDOWS,
                peak_rate=rate,
                offpeak_rate=rate,
            ),
        ),
        quota=Quota(day=TUESDAY, calls=calls),
    )
    assert stale_after(state=state) == limit
