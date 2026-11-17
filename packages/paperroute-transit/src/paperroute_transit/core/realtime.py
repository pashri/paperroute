"""Live trip updates, as plain data."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

EVERY_STOP: Final[int] = -1  # a stop sequence before every real one


@dataclass(frozen=True)
class StopUpdate:
    """Live information for one stop of a trip."""

    sequence: int | None
    stop_id: str | None
    arrival_delay: int | None
    departure_delay: int | None
    arrival_time: int | None
    departure_time: int | None


@dataclass(frozen=True)
class TripRealtime:
    """
    Live information for one trip.

    Attributes
    ----------
    estimated : bool
        Whether the times are an estimate from a position.
    uncertain : bool
        Whether the estimate is uncertain.
    heard_at : datetime | None
        When the position an estimate came from was reported, on the
        local clock; None for a feed's own times.
    """

    trip_id: str
    cancelled: bool
    updates: tuple[StopUpdate, ...]
    start_date: date | None
    estimated: bool = False
    uncertain: bool = False
    heard_at: datetime | None = None


@dataclass(frozen=True)
class RealtimeSnapshot:
    """One parsed download of a trip-updates feed."""

    fetched_at: datetime
    trips: Mapping[str, TripRealtime]
