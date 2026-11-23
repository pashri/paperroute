"""A route's trips on a service day, and which of them are running."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

from paperroute_transit.core.model import service_time


@dataclass(frozen=True)
class RouteTrip:
    """One trip of a route.

    Attributes
    ----------
    trip_id : str
        Trip ID.
    day : date
        Service day the trip belongs to.
    starts : int
        First departure, in seconds into the service day.
    ends : int
        Last arrival, in seconds into the service day.
    direction : int | None
        Direction of travel, or None when the feed gives none.
    """

    trip_id: str
    day: date
    starts: int
    ends: int
    direction: int | None


def running(
    *, trips: Sequence[RouteTrip], now: datetime, zone: tzinfo
) -> tuple[RouteTrip, ...]:
    """
    Keep the trips under way at a moment.

    A trip is under way from its first departure to its last arrival,
    both included. A trip of the day before can still be under way after
    midnight.

    Parameters
    ----------
    trips : Sequence[RouteTrip]
        Trips to choose from.
    now : datetime
        The moment.
    zone : tzinfo
        Local time zone of the service days.

    Returns
    -------
    tuple[RouteTrip, ...]
        The trips under way, in the order given.
    """
    return tuple(
        trip
        for trip in trips
        if service_time(day=trip.day, seconds=trip.starts, zone=zone)
        <= now
        <= service_time(day=trip.day, seconds=trip.ends, zone=zone)
    )
