"""Tests for the domain model."""

from datetime import UTC, date, datetime

import pytest

from paperroute.core.model import (
    SYDNEY,
    Mode,
    mode_for,
    service_epoch,
    service_seconds,
    service_time,
)


@pytest.mark.parametrize(('route_type', 'agency_id', 'mode'), [
    (2, 'x0001', Mode.TRAIN),
    (2, 'NSWTrains', Mode.TRAIN),
    (401, 'SMNW', Mode.METRO),
    (4, None, Mode.FERRY),
    (900, None, Mode.LIGHT_RAIL),
    (700, '2436', Mode.BUS),
    (712, None, Mode.BUS),
    (204, None, Mode.COACH),
    (106, '711', Mode.REGIONAL),
    (204, '711', Mode.REGIONAL),
    (1, None, Mode.OTHER),
])
def test_mode_for_route_types(
    route_type: int, agency_id: str | None, mode: Mode
) -> None:
    """GTFS route types and NSW TrainLink's agency map to modes."""
    assert mode_for(route_type=route_type, agency_id=agency_id) is mode


def test_service_epoch_is_midnight_on_normal_days() -> None:
    """On an ordinary day the service day starts at midnight."""
    assert service_epoch(day=date(2026, 10, 16)) == datetime(
        2026, 10, 16, tzinfo=SYDNEY
    )


def test_service_epoch_on_daylight_saving_start() -> None:
    """When clocks go forward, noon minus 12 hours is 23:00 the day before."""
    epoch = service_epoch(day=date(2026, 10, 4))
    assert epoch == datetime(2026, 10, 3, 23, tzinfo=SYDNEY)
    assert epoch.astimezone(UTC) == datetime(2026, 10, 3, 13, tzinfo=UTC)


def test_service_time_after_daylight_saving_start() -> None:
    """A GTFS 08:00:00 on the change day is 8 am on the new clock."""
    eight = service_time(day=date(2026, 10, 4), seconds=8 * 3600)
    assert eight.astimezone(UTC) == datetime(2026, 10, 3, 21, tzinfo=UTC)
    assert (eight.hour, eight.minute) == (8, 0)


def test_service_seconds_inverts_service_time() -> None:
    """Seconds since the epoch round-trip through a Sydney time."""
    moment = service_time(day=date(2026, 10, 4), seconds=90600)
    assert service_seconds(day=date(2026, 10, 4), moment=moment) == 90600
