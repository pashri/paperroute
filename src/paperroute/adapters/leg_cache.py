"""Scheduled departures per leg and service day, kept in memory."""

import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol

from paperroute.adapters.carry_on_rows import first_to_arrive
from paperroute.core.model import SYDNEY, Departure, Pin

CACHE_LIMIT: Final[int] = 12

type CacheKey = tuple[Pin, date]


class DepartureReader(Protocol):
    """Anything that reads a pin's scheduled departures."""

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Read the services leaving the pin's From between two moments.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            In departure order.
        """


@dataclass(frozen=True)
class Covered:
    """The departures read for one leg over one range."""

    start: datetime
    end: datetime
    items: tuple[Departure, ...]


def merged(
    *, old: Covered, fresh: list[Departure], end: datetime, pin: Pin,
) -> Covered:
    """
    Extend a covered range with the departures read past its end.

    Parameters
    ----------
    old : Covered
        The range held.
    fresh : list[Departure]
        Departures read from old's end to end.
    end : datetime
        New end.
    pin : Pin
        Leg; with a Destination, carry-ons a later direct trip beats are
        dropped again over the whole range.

    Returns
    -------
    Covered
        From old's start to end, each departure once, in order.
    """
    seen = {identity(item=item): item for item in (*old.items, *fresh)}
    items = sorted(
        seen.values(), key=lambda item: (item.departs, item.trip_id),
    )
    if pin.destination is not None:
        items = first_to_arrive(departures=items)
    return Covered(start=old.start, end=end, items=tuple(items))


def identity(*, item: Departure) -> tuple[str, date, int]:
    """
    Name one call of one trip.

    Parameters
    ----------
    item : Departure
        Departure.

    Returns
    -------
    tuple[str, date, int]
        Trip, service day and From stop sequence.
    """
    return item.trip_id, item.service_day, item.origin_seq


class LegCache:
    """
    Keep each leg's scheduled departures for the day it is read on.

    A later read of a leg on the same Sydney day reads only past the end
    already held. Older days are dropped, and at most `CACHE_LIMIT` legs
    are held. A change of timetable version empties the cache. Realtime
    is not held: callers overlay it on every read.
    """

    def __init__(self) -> None:
        self.held: dict[CacheKey, Covered] = {}
        self.version: str | None = None
        self.lock = threading.Lock()

    def size(self) -> int:
        """
        Count the legs held.

        Returns
        -------
        int
            Legs held.
        """
        return len(self.held)

    def departures(
        self,
        *,
        timetable: DepartureReader,
        pin: Pin,
        start: datetime,
        end: datetime,
        version: str | None = None,
    ) -> list[Departure]:
        """
        Read a leg's departures, from memory where the range is held.

        Parameters
        ----------
        timetable : DepartureReader
            Timetable read for any part not held.
        pin : Pin
            Leg.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.
        version : str | None
            Timetable version the read is for.

        Returns
        -------
        list[Departure]
            The departures leaving from start to end, in order.
        """
        key = (pin, start.astimezone(SYDNEY).date())
        with self.lock:
            self.forget(day=key[1], version=version)
            covered = self.cover(
                timetable=timetable, key=key, start=start, end=end,
            )
            self.store(key=key, covered=covered)
        return [
            item for item in covered.items if start <= item.departs <= end
        ]

    def cover(
        self,
        *,
        timetable: DepartureReader,
        key: CacheKey,
        start: datetime,
        end: datetime,
    ) -> Covered:
        """
        Find or read a range covering start to end.

        Parameters
        ----------
        timetable : DepartureReader
            Timetable.
        key : CacheKey
            Leg and day.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        Covered
            The held range, extended past its end when needed, or a fresh
            read when nothing usable is held.
        """
        old = self.held.get(key)
        pin = key[0]
        if old is None or start < old.start or start > old.end:
            fresh = timetable.departures(pin=pin, start=start, end=end)
            return Covered(start=start, end=end, items=tuple(fresh))
        if end <= old.end:
            return old
        fresh = timetable.departures(pin=pin, start=old.end, end=end)
        return merged(old=old, fresh=fresh, end=end, pin=pin)

    def forget(self, *, day: date, version: str | None) -> None:
        """
        Drop legs of other days, or everything for a new version.

        Parameters
        ----------
        day : date
            Sydney day being read.
        version : str | None
            Timetable version being read.
        """
        if version != self.version:
            self.held.clear()
            self.version = version
        for key in [key for key in self.held if key[1] != day]:
            del self.held[key]

    def store(self, *, key: CacheKey, covered: Covered) -> None:
        """
        Hold a leg's range, dropping the oldest past the cap.

        Parameters
        ----------
        key : CacheKey
            Leg and day.
        covered : Covered
            Range read.
        """
        self.held.pop(key, None)
        self.held[key] = covered
        while len(self.held) > CACHE_LIMIT:
            del self.held[next(iter(self.held))]
