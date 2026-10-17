"""Live vehicle positions, as plain data."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Final

from paperroute.core.geo import Point

LABEL_LENGTH: Final[int] = 4


@dataclass(frozen=True)
class VehiclePosition:
    """One vehicle's reported position."""

    trip_id: str
    route_id: str
    label: str
    point: Point
    bearing: float | None
    timestamp: int
    start_date: date | None


@dataclass(frozen=True)
class PositionsSnapshot:
    """Every vehicle in one positions feed download."""

    fetched_at: datetime
    header_time: int
    vehicles: tuple[VehiclePosition, ...]


def fallback_label(*, route_id: str) -> str:
    """
    Make a short label from a route ID.

    Parameters
    ----------
    route_id : str
        Route ID, for example '2503_80'.

    Returns
    -------
    str
        The text after the last underscore cut to four characters, or '?'
        when that is empty.
    """
    return route_id.rsplit('_', 1)[-1][:LABEL_LENGTH] or '?'


def with_labels(
    *, snapshot: PositionsSnapshot, labels: Mapping[str, str]
) -> PositionsSnapshot:
    """
    Replace the label of every vehicle whose trip is in a mapping.

    Parameters
    ----------
    snapshot : PositionsSnapshot
        Snapshot to relabel.
    labels : Mapping[str, str]
        Label by trip ID.

    Returns
    -------
    PositionsSnapshot
        A new snapshot; vehicles on other trips keep their labels.
    """
    return replace(
        snapshot,
        vehicles=tuple(
            replace(vehicle, label=labels[vehicle.trip_id])
            if vehicle.trip_id in labels
            else vehicle
            for vehicle in snapshot.vehicles
        ),
    )
