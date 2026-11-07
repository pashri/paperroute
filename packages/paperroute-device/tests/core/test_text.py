"""Tests for the text shared by the device's screens."""

from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_device.core.text import clock_text

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
