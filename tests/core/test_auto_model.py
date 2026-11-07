"""Tests for auto windows on pins and their occurrences."""

from dataclasses import replace
from datetime import datetime, time
from typing import Final

from paperroute.core.auto_model import (
    AutoState,
    Occurrence,
    holds_awake,
    running,
)
from paperroute.core.model import SYDNEY, AutoWindow, Pin, pin_ref, same_place
from paperroute_testkit.core.factories import START, pin_of

WEEKDAYS: Final[frozenset[int]] = frozenset(range(5))
PIN_A: Final[Pin] = pin_of('A', None)
PIN_B: Final[Pin] = pin_of('B', None)


def at(*, day: int, hour: int, minute: int = 0) -> datetime:
    """
    A Sydney time in October 2026.

    Parameters
    ----------
    day : int
        Day of the month.
    hour : int
        Hour.
    minute : int
        Minute.

    Returns
    -------
    datetime
        Aware Sydney datetime.
    """
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY)


def windowed(*, pin: Pin, window: AutoWindow) -> Pin:
    """
    Give a pin one window.

    Parameters
    ----------
    pin : Pin
        Pin.
    window : AutoWindow
        Window.

    Returns
    -------
    Pin
        The pin with only that window.
    """
    return replace(pin, windows=(window,))


def test_weekday_window_runs() -> None:
    """A weekday window runs from its start up to, not at, its end."""
    pin = windowed(
        pin=PIN_A, window=AutoWindow(WEEKDAYS, time(7, 30), time(9)),
    )
    assert running(pins=(pin,), now=START) == Occurrence(
        ref=pin_ref(pin=pin),
        start=at(day=16, hour=7, minute=30),
        end=at(day=16, hour=9),
    )
    assert running(pins=(pin,), now=at(day=16, hour=9)) is None
    assert running(pins=(pin,), now=at(day=16, hour=7, minute=29)) is None


def test_midnight_window_runs_into_next_day() -> None:
    """A window that crosses midnight runs into the next day."""
    pin = windowed(
        pin=PIN_A, window=AutoWindow(frozenset({4}), time(22), time(1)),
    )
    found = running(pins=(pin,), now=at(day=17, hour=0, minute=30))
    assert found is not None
    assert found.end == at(day=17, hour=1)
    assert running(pins=(pin,), now=at(day=17, hour=1)) is None


def test_days_name_the_start_day() -> None:
    """A window's days name the day it starts."""
    pin = windowed(
        pin=PIN_A, window=AutoWindow(frozenset({5}), time(22), time(1)),
    )
    assert running(pins=(pin,), now=at(day=17, hour=0, minute=30)) is None


def test_latest_start_wins() -> None:
    """The running occurrence with the latest start wins."""
    long = windowed(
        pin=PIN_A, window=AutoWindow(WEEKDAYS, time(7), time(10)),
    )
    short = windowed(
        pin=PIN_B, window=AutoWindow(WEEKDAYS, time(8, 30), time(9)),
    )
    pins = (long, short)
    inner = running(pins=pins, now=at(day=16, hour=8, minute=45))
    assert inner is not None
    assert inner.ref == pin_ref(pin=short)
    assert running(pins=pins, now=at(day=16, hour=9, minute=30)) == (
        Occurrence(
            ref=pin_ref(pin=long),
            start=at(day=16, hour=7),
            end=at(day=16, hour=10),
        )
    )


def test_tie_goes_to_earlier_pin() -> None:
    """Two windows starting together give the earlier pin."""
    window = AutoWindow(WEEKDAYS, time(7), time(10))
    pins = (
        windowed(pin=PIN_A, window=window),
        windowed(pin=PIN_B, window=window),
    )
    found = running(pins=pins, now=START)
    assert found is not None
    assert found.ref == pin_ref(pin=PIN_A)


def test_windows_ignored_by_identity() -> None:
    """Windows change neither a pin's ref nor whether it is the same."""
    pin = windowed(
        pin=PIN_A, window=AutoWindow(WEEKDAYS, time(7), time(10)),
    )
    assert pin_ref(pin=pin) == pin_ref(pin=PIN_A)
    assert same_place(first=pin, second=PIN_A)


def test_holds_awake() -> None:
    """Only a recorded occurrence holds the panel awake."""
    occurrence = Occurrence(
        ref='r', start=at(day=16, hour=7), end=at(day=16, hour=9),
    )
    assert holds_awake(auto=AutoState(occurrence=occurrence))
    assert not holds_awake(auto=AutoState())


def test_window_on_spring_forward_day() -> None:
    """A start in the skipped hour runs from an hour later."""
    pin = windowed(
        pin=PIN_A, window=AutoWindow(frozenset({6}), time(2, 30), time(4)),
    )
    late = datetime(2026, 10, 4, 3, 30, tzinfo=SYDNEY)
    early = datetime(2026, 10, 4, 3, tzinfo=SYDNEY)
    assert running(pins=(pin,), now=early) is None
    found = running(pins=(pin,), now=late)
    assert found is not None
    assert found.start == late


def test_window_on_fall_back_day() -> None:
    """A start in the repeated hour runs once, in the first pass."""
    pin = windowed(
        pin=PIN_A, window=AutoWindow(frozenset({6}), time(2), time(2, 45)),
    )
    first = datetime(2026, 4, 5, 2, 10, tzinfo=SYDNEY)
    assert running(pins=(pin,), now=first) is not None
    assert running(pins=(pin,), now=first.replace(fold=1)) is None
