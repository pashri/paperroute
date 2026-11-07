"""Tests for vehicle position data."""

from datetime import datetime

from paperroute_transit.core.model import SYDNEY
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
    fallback_label,
    with_labels,
)


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
        fetched_at=datetime(2026, 10, 16, tzinfo=SYDNEY),
        header_time=5,
        vehicles=(
            vehicle(trip_id='a', label='x'),
            vehicle(trip_id='b', label='y'),
        ),
    )
    result = with_labels(snapshot=snapshot, labels={'a': '606'})
    assert [v.label for v in result.vehicles] == ['606', 'y']
    assert result.header_time == 5
