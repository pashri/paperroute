"""Tests for clocks."""

from datetime import datetime
from pathlib import Path

from paperroute.adapters.clocks import ReplayClock, SystemClock
from paperroute.core.model import SYDNEY


def test_system_clock_is_sydney_aware() -> None:
    """The system clock reports Sydney time."""
    assert SystemClock().now().tzinfo is SYDNEY


def test_replay_clock_starts_at_start() -> None:
    """A replay clock begins at its start time and moves forward."""
    start = datetime(2026, 10, 16, 6, 30, tzinfo=SYDNEY)
    clock = ReplayClock(start=start)
    assert start <= clock.now() < start.replace(minute=31)


def test_replay_clock_adds_real_seconds_across_dst() -> None:
    """Two real seconds after 01:59:59 on the DST day is 03:00:01."""
    start = datetime(2026, 10, 4, 1, 59, 59, tzinfo=SYDNEY)
    clock = ReplayClock(start=start)
    clock.began -= 2
    expected = datetime(2026, 10, 4, 3, 0, 1, tzinfo=SYDNEY)
    assert clock.now() == expected
    assert clock.now().hour == expected.hour


def test_system_clock_synced_follows_flag(tmp_path: Path) -> None:
    """The system clock is synchronised once the flag file exists."""
    flag = tmp_path / 'synchronized'
    clock = SystemClock(flag=flag)
    assert not clock.synced()
    flag.touch()
    assert clock.synced()


def test_replay_clock_is_synced() -> None:
    """A replay clock is always synchronised."""
    start = datetime(2026, 10, 16, 6, 30, tzinfo=SYDNEY)
    assert ReplayClock(start=start).synced()
