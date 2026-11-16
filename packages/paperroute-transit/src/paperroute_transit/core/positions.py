"""Live vehicle positions, as plain data."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final

from paperroute_transit.core.geo import Point

LABEL_LENGTH: Final[int] = 4


class ParkedAge(StrEnum):
    """How long since a vehicle was heard before it is hidden."""

    OFF = 'off'
    FIVE = '5min'
    TEN = '10min'
    THIRTY = '30min'
    HOUR = '60min'


PARKED: Final[dict[ParkedAge, timedelta | None]] = {
    ParkedAge.OFF: None,
    ParkedAge.FIVE: timedelta(minutes=5),
    ParkedAge.TEN: timedelta(minutes=10),
    ParkedAge.THIRTY: timedelta(minutes=30),
    ParkedAge.HOUR: timedelta(hours=1),
}


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


def with_route_labels(
    *,
    snapshot: PositionsSnapshot,
    trips: Collection[str],
    routes: Mapping[str, str],
) -> PositionsSnapshot:
    """
    Draw a vehicle by its route when its trip is not in the timetable.

    Parameters
    ----------
    snapshot : PositionsSnapshot
        Snapshot to relabel.
    trips : Collection[str]
        Trip IDs the timetable knows.
    routes : Mapping[str, str]
        Route short name by feed route ID.

    Returns
    -------
    PositionsSnapshot
        A new snapshot. A vehicle on a known trip is kept; of the rest,
        one with a label is kept, one on a known route takes the route's
        name, and the others are left out.
    """
    kept: list[VehiclePosition] = []
    for vehicle in snapshot.vehicles:
        if vehicle.trip_id in trips or vehicle.label:
            kept.append(vehicle)
        elif vehicle.route_id in routes:
            kept.append(replace(vehicle, label=routes[vehicle.route_id]))
    return replace(snapshot, vehicles=tuple(kept))


def without_parked(
    *, snapshot: PositionsSnapshot, limit: timedelta | None
) -> PositionsSnapshot:
    """
    Leave out vehicles last heard longer ago than a limit.

    Parameters
    ----------
    snapshot : PositionsSnapshot
        Snapshot to filter.
    limit : timedelta | None
        The longest age kept, measured from the feed's header time; None
        keeps every vehicle. A timestamp of 0 counts as just heard.

    Returns
    -------
    PositionsSnapshot
        The snapshot itself when there is no limit, else a new one.
    """
    if limit is None:
        return snapshot
    seconds = limit.total_seconds()
    return replace(
        snapshot,
        vehicles=tuple(
            vehicle
            for vehicle in snapshot.vehicles
            if not vehicle.timestamp
            or snapshot.header_time - vehicle.timestamp <= seconds
        ),
    )
