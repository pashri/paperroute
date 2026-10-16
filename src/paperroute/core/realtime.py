"""Live trip updates, as plain data."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime


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
    """Live information for one trip."""

    trip_id: str
    cancelled: bool
    updates: tuple[StopUpdate, ...]
    start_date: date | None


@dataclass(frozen=True)
class RealtimeSnapshot:
    """One parsed download of a trip-updates feed."""

    fetched_at: datetime
    trips: Mapping[str, TripRealtime]
