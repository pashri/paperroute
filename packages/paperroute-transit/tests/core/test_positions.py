"""Tests for vehicle position data."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
    fallback_label,
    with_labels,
    without_parked,
)

ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')


def vehicle(*, trip_id: str, label: str) -> VehiclePosition:
    """
    Build a position on a trip.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    label : str
        Display label.

    Returns
    -------
    VehiclePosition
        A position at the origin.
    """
    return VehiclePosition(
        trip_id=trip_id,
        route_id='r',
        label=label,
        point=(0.0, 0.0),
        bearing=None,
        timestamp=1,
        start_date=None,
    )


def test_fallback_label() -> None:
    """The label is the route ID's last part, cut to four characters."""
    assert fallback_label(route_id='2503_80') == '80'
    assert fallback_label(route_id='_461X') == '461X'
    assert fallback_label(route_id='') == '?'
    assert fallback_label(route_id='a_ABCDEFG') == 'ABCD'
    assert fallback_label(route_id='a_b_c') == 'c'


def test_with_labels_replaces_known_trips_only() -> None:
    """Trips in the mapping get the new label; the rest keep theirs."""
    snapshot = PositionsSnapshot(
        fetched_at=datetime(2026, 10, 16, tzinfo=ZONE),
        header_time=5,
        vehicles=(
            vehicle(trip_id='a', label='x'),
            vehicle(trip_id='b', label='y'),
        ),
    )
    result = with_labels(snapshot=snapshot, labels={'a': '606'})
    assert [v.label for v in result.vehicles] == ['606', 'y']
    assert result.header_time == 5


SNAPSHOT: Final[PositionsSnapshot] = PositionsSnapshot(
    fetched_at=datetime(2026, 10, 16, tzinfo=ZONE),
    header_time=1000,
    vehicles=tuple(
        replace(vehicle(trip_id=str(stamp), label='x'), timestamp=stamp)
        for stamp in (1000, 400, 399, 0, 2000)
    ),
)


def test_without_parked_drops_vehicles_heard_before_the_limit() -> None:
    """Without parked drops vehicles heard before the limit."""
    kept = without_parked(snapshot=SNAPSHOT, limit=timedelta(minutes=10))
    assert [v.timestamp for v in kept.vehicles] == [1000, 400, 0, 2000]


def test_without_parked_off_returns_the_same_snapshot() -> None:
    """Without parked off returns the same snapshot."""
    assert without_parked(snapshot=SNAPSHOT, limit=None) is SNAPSHOT
