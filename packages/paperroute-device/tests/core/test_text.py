"""Tests for the text shared by the device's screens."""

from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_device.core.text import clock_text, day_text, local_time

ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
START: Final[datetime] = datetime(2026, 10, 16, 8, 0, tzinfo=ZONE)


def test_clock_text_has_no_leading_zero() -> None:
    """Clock times look like 8:05 and 13:40."""
    moment = START + timedelta(minutes=5)
    assert clock_text(moment=moment, zone=ZONE) == '8:05'
    later = START + timedelta(hours=5, minutes=40)
    assert clock_text(moment=later, zone=ZONE) == '13:40'


def test_clock_text_uses_given_zone() -> None:
    """The same moment reads as each zone's own wall-clock time."""
    moment = datetime(2026, 10, 15, 21, 5, tzinfo=UTC)
    assert clock_text(moment=moment, zone=ZONE) == '8:05'
    london = ZoneInfo('Europe/London')
    assert clock_text(moment=moment, zone=london) == '22:05'


def test_local_time_uses_given_zone() -> None:
    """The same stamp reads as each zone's own wall-clock time."""
    stamp = '2026-10-15T21:05:00+00:00'
    here = local_time(stamp=stamp, zone=ZONE)
    london = local_time(stamp=stamp, zone=ZoneInfo('Europe/London'))
    assert here is not None and london is not None
    assert (here.hour, here.minute) == (8, 5)
    assert (london.hour, london.minute) == (22, 5)


def test_local_time_missing_or_unreadable() -> None:
    """No stamp, or one that is not ISO 8601, gives None."""
    assert local_time(stamp=None, zone=ZONE) is None
    assert local_time(stamp='yesterday', zone=ZONE) is None


def test_day_text_short_month() -> None:
    """Days read as the day of the month and a short month."""
    assert day_text(moment=START) == '16 Oct'
