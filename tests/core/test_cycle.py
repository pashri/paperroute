"""Tests for the poll cycle timing."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from paperroute.core.alert_state import ALERT_EVERY
from paperroute.core.cycle import alerts_due, boundary, cycle_due
from paperroute.core.state_model import Cycle

SYDNEY = ZoneInfo('Australia/Sydney')


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 6, hour, minute, second, tzinfo=SYDNEY)


@pytest.mark.parametrize(
    ('now', 'every', 'expected'),
    [
        (_at(10, 0, 47), timedelta(seconds=30), _at(10, 0, 30)),
        (_at(10, 0, 47), timedelta(seconds=60), _at(10, 0, 0)),
        (_at(10, 3, 10), timedelta(minutes=2), _at(10, 2, 0)),
        (_at(10, 4, 59), timedelta(minutes=5), _at(10, 0, 0)),
    ],
)
def test_boundary_aligns_to_clock(
    now: datetime,
    every: timedelta,
    expected: datetime,
) -> None:
    assert boundary(now=now, every=every) == expected


def test_boundary_on_fall_back_night() -> None:
    every = timedelta(seconds=60)
    start = datetime(2026, 4, 5, 1, 58, tzinfo=SYDNEY)
    found = [
        boundary(
            now=datetime.fromtimestamp(
                start.timestamp() + 45 * i,
                tz=SYDNEY,
            ),
            every=every,
        )
        for i in range(240)
    ]
    stamps = [moment.timestamp() for moment in found]
    distinct = sorted(set(stamps))
    assert len(distinct) > 100
    gaps = {
        later - earlier
        for earlier, later in zip(distinct, distinct[1:])
    }
    assert gaps == {60.0}
    assert stamps == sorted(stamps)


def test_cycle_due_after_clock_steps_back() -> None:
    now = _at(10, 0, 5)
    cycle = Cycle(started=now + timedelta(hours=1))
    assert cycle_due(cycle=cycle, now=now, every=timedelta(seconds=30))


def test_cycle_due_when_none_started() -> None:
    assert cycle_due(
        cycle=Cycle(),
        now=_at(10, 0, 5),
        every=timedelta(seconds=30),
    )


def test_cycle_due_only_after_boundary() -> None:
    every = timedelta(seconds=30)
    cycle = Cycle(started=_at(10, 0, 31))
    assert not cycle_due(cycle=cycle, now=_at(10, 0, 59), every=every)
    assert cycle_due(cycle=cycle, now=_at(10, 1, 1), every=every)


def test_alerts_due_only_on_five_minute_boundary() -> None:
    cycle = Cycle(alerts_at=_at(10, 0, 3))
    assert ALERT_EVERY == timedelta(minutes=5)
    assert not alerts_due(cycle=cycle, now=_at(10, 4, 59))
    assert alerts_due(cycle=cycle, now=_at(10, 5, 1))
    assert alerts_due(cycle=Cycle(), now=_at(10, 5, 1))
