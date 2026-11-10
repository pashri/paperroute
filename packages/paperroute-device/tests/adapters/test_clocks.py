"""Tests for clocks."""

from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_device.adapters.clocks import ReplayClock, SystemClock

ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')


def test_system_clock_is_zone_aware() -> None:
    """The system clock reports local time."""
    assert SystemClock(zone=ZONE).now().tzinfo is ZONE


def test_replay_clock_starts_at_start() -> None:
    """A replay clock begins at its start time and moves forward."""
    start = datetime(2026, 10, 16, 6, 30, tzinfo=ZONE)
    clock = ReplayClock(start=start, zone=ZONE)
    assert start <= clock.now() < start.replace(minute=31)


def test_replay_clock_adds_real_seconds_across_dst() -> None:
    """Two real seconds after 01:59:59 on the DST day is 03:00:01."""
    start = datetime(2026, 10, 4, 1, 59, 59, tzinfo=ZONE)
    clock = ReplayClock(start=start, zone=ZONE)
    clock.began -= 2
    expected = datetime(2026, 10, 4, 3, 0, 1, tzinfo=ZONE)
    assert clock.now() == expected
    assert clock.now().hour == expected.hour


def test_system_clock_synced_follows_flag(tmp_path: Path) -> None:
    """The system clock is synchronised once the flag file exists."""
    flag = tmp_path / 'synchronized'
    clock = SystemClock(flag=flag, zone=ZONE)
    assert not clock.synced()
    flag.touch()
    assert clock.synced()


def test_replay_clock_is_synced() -> None:
    """A replay clock is always synchronised."""
    start = datetime(2026, 10, 16, 6, 30, tzinfo=ZONE)
    assert ReplayClock(start=start, zone=ZONE).synced()
