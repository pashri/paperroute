"""Tests for the domain model."""

from datetime import UTC, date, datetime

import pytest

from paperroute.core.model import (
    SYDNEY,
    Mode,
    RouteKind,
    kind_for,
    mode_for,
    service_epoch,
    service_seconds,
    service_time,
    until_repeated_hop,
    until_revisit,
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


def test_service_time_before_daylight_saving_start() -> None:
    """A GTFS 01:00:00 on the change day is before the clocks jump."""
    one = service_time(day=date(2026, 10, 4), seconds=3600)
    assert one.astimezone(UTC) == datetime(2026, 10, 3, 14, tzinfo=UTC)


@pytest.mark.parametrize(('route_type', 'agency_id', 'short_name', 'kind'), [
    (2, 'SydneyTrains', 'T1', RouteKind.TRAIN),
    (2, 'SydneyTrains', 'CCN', RouteKind.TRAIN),
    (2, 'NSWTrains', 'NRC', RouteKind.REGIONAL),
    (2, 'SydneyTrains', '', None),
    (700, '2436', '', None),
    (106, None, '31', RouteKind.REGIONAL),
    (204, None, '700', RouteKind.REGIONAL),
    (205, None, '701', RouteKind.REGIONAL),
    (700, '711', '480', RouteKind.REGIONAL),
    (2, '711', 'T1', RouteKind.REGIONAL),
    (401, 'SMNW', 'M1', RouteKind.METRO),
    (900, None, 'L1', RouteKind.LIGHT_RAIL),
    (4, None, 'F1', RouteKind.FERRY),
    (700, '2436', '606', RouteKind.BUS),
    (712, '2436', '2606', RouteKind.SCHOOL),
    (714, '2436', 'RTTA', RouteKind.REPLACEMENT),
    (715, '2436', 'OD1', None),
])
def test_kind_for_sorts_every_route_type(
    route_type: int, agency_id: str | None, short_name: str,
    kind: RouteKind | None,
) -> None:
    """Route types, agencies and line names sort into chooser kinds."""
    assert kind_for(
        route_type=route_type, agency_id=agency_id, short_name=short_name,
    ) == kind


def test_until_revisit_cuts_before_a_repeat() -> None:
    """The run stops before the first station seen twice."""
    assert until_revisit(stations=['A', 'B', 'C', 'B', 'D']) == (
        'A', 'B', 'C',
    )
    assert until_revisit(stations=['A', 'B']) == ('A', 'B')


def test_until_repeated_hop_keeps_a_loop() -> None:
    """A loop through a station twice stays whole until a hop repeats."""
    loop = ('C', 'L1', 'L2', 'J', 'C', 'E1')
    assert until_repeated_hop(stations=loop) == loop
    assert until_repeated_hop(stations=['A', 'B', 'C', 'A', 'B', 'D']) == (
        'A', 'B', 'C', 'A',
    )
