"""Reported vehicles, picked once per positions download."""

from collections.abc import Mapping
from datetime import datetime
from typing import Final

from paperroute_transit.core.memo import SameMemo
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)

type Heard = tuple[VehiclePosition, PositionsSnapshot]

HEARD_MEMO_SIZE: Final[int] = 4  # positions mappings held
HEARD_MEMO: Final[SameMemo[tuple[Heard, ...]]] = SameMemo(
    size=HEARD_MEMO_SIZE,
)


def position_age(
    *,
    vehicle: VehiclePosition,
    snapshot: PositionsSnapshot,
    now: datetime,
) -> float:
    """
    Find how long ago a vehicle reported, on the feed server's clock.

    Parameters
    ----------
    vehicle : VehiclePosition
        The vehicle; a timestamp of 0 counts as the feed header time.
    snapshot : PositionsSnapshot
        The download it came in.
    now : datetime
        The frame time.

    Returns
    -------
    float
        The header time minus the vehicle's timestamp, plus the seconds
        since the download.
    """
    lag = snapshot.header_time - (vehicle.timestamp or snapshot.header_time)
    return lag + (now - snapshot.fetched_at).total_seconds()


def reported_at(*, heard: Heard) -> float:
    """
    Order a vehicle by when it reported, whatever the frame time.

    Parameters
    ----------
    heard : Heard
        A vehicle and its download.

    Returns
    -------
    float
        Smaller for a vehicle heard more recently.
    """
    vehicle, snapshot = heard
    lag = snapshot.header_time - (vehicle.timestamp or snapshot.header_time)
    return lag - snapshot.fetched_at.timestamp()


def pick_vehicles(
    *, positions: Mapping[str, PositionsSnapshot],
) -> tuple[Heard, ...]:
    """
    Keep the most recently heard vehicle of each trip.

    Parameters
    ----------
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.

    Returns
    -------
    tuple[Heard, ...]
        Each vehicle with its download. Vehicles with no trip ID are all
        kept.
    """
    best: dict[tuple[str, int], Heard] = {}
    for feed, snapshot in positions.items():
        for index, vehicle in enumerate(snapshot.vehicles):
            key = (vehicle.trip_id, -1) if vehicle.trip_id else (feed, index)
            found = (vehicle, snapshot)
            old = best.get(key)
            if old is None or reported_at(heard=found) < reported_at(
                heard=old,
            ):
                best[key] = found
    return tuple(best.values())


def heard_vehicles(
    *, positions: Mapping[str, PositionsSnapshot],
) -> tuple[Heard, ...]:
    """
    List every reported vehicle, picked once per download.

    Parameters
    ----------
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed. The result is reused
        while the feeds and the very download objects are the same, even
        in a new mapping.

    Returns
    -------
    tuple[Heard, ...]
        Each vehicle with its download; age it with `position_age`.
    """
    inputs = tuple(positions.values())
    same = tuple(positions)
    held = HEARD_MEMO.get(inputs=inputs, same=same)
    if held is not None:
        return held
    return HEARD_MEMO.put(
        inputs=inputs, same=same, value=pick_vehicles(positions=positions),
    )
