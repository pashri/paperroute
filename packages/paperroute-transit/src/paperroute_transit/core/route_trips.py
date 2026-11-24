"""A route's trips on a service day, and which of them are running."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Final

from paperroute_transit.core.memo import SameMemo
from paperroute_transit.core.model import service_time

type Spans = tuple[tuple[datetime, datetime, RouteTrip], ...]

SPANS: Final[SameMemo[Spans]] = SameMemo(size=4)


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
    spans = SPANS.get(inputs=(trips, zone))
    if spans is None:
        spans = SPANS.put(
            inputs=(trips, zone), value=trip_spans(trips=trips, zone=zone),
        )
    return tuple(trip for first, last, trip in spans if first <= now <= last)


def trip_spans(*, trips: Sequence[RouteTrip], zone: tzinfo) -> Spans:
    """
    Give each trip's first departure and last arrival as moments.

    Parameters
    ----------
    trips : Sequence[RouteTrip]
        The trips.
    zone : tzinfo
        Local time zone of the service days.

    Returns
    -------
    Spans
        Each trip's first and last moment, then the trip, in the order
        given.
    """
    return tuple(
        (
            service_time(day=trip.day, seconds=trip.starts, zone=zone),
            service_time(day=trip.day, seconds=trip.ends, zone=zone),
            trip,
        )
        for trip in trips
    )
