"""Tests for the default Trip Planner search."""

from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from paperroute.core.trip_search import TripSearch, default_search

SYDNEY: Final = ZoneInfo('Australia/Sydney')

FRIDAY_LATE: Final[datetime] = datetime(2026, 10, 30, 10, tzinfo=SYDNEY)
SATURDAY: Final[datetime] = datetime(2026, 10, 31, 7, tzinfo=SYDNEY)
MONDAY_NINE: Final[datetime] = datetime(2026, 11, 2, 9, tzinfo=SYDNEY)


def at(*, day: int, hour: int, minute: int = 0) -> datetime:
    """
    Make a Sydney time in October 2026 (the 26th is a Monday).

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
        The time.
    """
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY)


@pytest.mark.parametrize(('now', 'expected'), [
    (at(day=26, hour=8), at(day=26, hour=9)),
    (at(day=26, hour=9, minute=1), at(day=27, hour=9)),
    (at(day=26, hour=9), at(day=27, hour=9)),
    (FRIDAY_LATE, MONDAY_NINE),
    (SATURDAY, MONDAY_NINE),
    (at(day=25, hour=12), at(day=26, hour=9)),
])
def test_default_search(now: datetime, expected: datetime) -> None:
    """The default is arrive-by 9:00 on the next weekday still ahead."""
    assert default_search(now=now) == TripSearch(when=expected, arrive=True)
