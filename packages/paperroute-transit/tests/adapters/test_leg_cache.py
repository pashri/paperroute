"""Tests for the in-memory cache of scheduled leg departures."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from paperroute_testkit.core.connection_factories import at
from paperroute_testkit.core.factories import departure, pin_of
from paperroute_transit.adapters.leg_cache import CACHE_LIMIT, LegCache
from paperroute_transit.core.model import CarryOn, Departure, Pin, TripFacts

PIN: Final[Pin] = pin_of('a', 'b')
ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')

type Call = tuple[Pin, datetime, datetime]


@dataclass
class ScheduleReader:
    """A timetable answering from a fixed schedule and noting each read."""

    schedule: list[Departure] = field(default_factory=list)
    reads: list[Call] = field(default_factory=list)

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Note the call and give the schedule's departures in the range.

        Parameters
        ----------
        pin : Pin
            The pin asked about.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            Departures leaving from start to end inclusive.
        """
        self.reads.append((pin, start, end))
        return [
            item for item in self.schedule if start <= item.departs <= end
        ]


def every_ten(*, first: datetime, count: int) -> list[Departure]:
    """
    Make departures ten minutes apart.

    Parameters
    ----------
    first : datetime
        First departure.
    count : int
        How many.

    Returns
    -------
    list[Departure]
        The departures.
    """
    return [
        departure(departs=first + timedelta(minutes=10 * index))
        for index in range(count)
    ]


def trips(*, items: list[Departure]) -> list[str]:
    """
    Name departures by trip.

    Parameters
    ----------
    items : list[Departure]
        Departures.

    Returns
    -------
    list[str]
        Trip IDs in order.
    """
    return [item.trip_id for item in items]


def test_departures_first_read_goes_to_timetable() -> None:
    """The first read of a leg asks the timetable for the range."""
    reader = ScheduleReader(schedule=every_ten(first=at(7, 0), count=30))
    found = LegCache(zone=ZONE).departures(
        timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0),
    )
    assert reader.reads == [(PIN, at(7, 0), at(8, 0))]
    assert len(found) == 7


def test_departures_repeat_read_is_cached() -> None:
    """A second read of the same range asks the timetable nothing."""
    reader = ScheduleReader(schedule=every_ten(first=at(7, 0), count=30))
    cache = LegCache(zone=ZONE)
    first = cache.departures(
        timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0),
    )
    second = cache.departures(
        timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0),
    )
    assert len(reader.reads) == 1
    assert second == first


def test_departures_later_end_reads_only_the_gap() -> None:
    """A longer range reads only past the cached end, without repeats."""
    schedule = every_ten(first=at(7, 0), count=30)
    reader = ScheduleReader(schedule=schedule)
    cache = LegCache(zone=ZONE)
    cache.departures(timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0))
    found = cache.departures(
        timetable=reader, pin=PIN, start=at(7, 5), end=at(9, 0),
    )
    assert reader.reads[1] == (PIN, at(8, 0), at(9, 0))
    assert trips(items=found) == trips(items=schedule[1:13])


def test_departures_earlier_start_rereads() -> None:
    """A range starting before the cached one is read in full."""
    reader = ScheduleReader(schedule=every_ten(first=at(6, 0), count=30))
    cache = LegCache(zone=ZONE)
    cache.departures(timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0))
    found = cache.departures(
        timetable=reader, pin=PIN, start=at(6, 0), end=at(7, 0),
    )
    assert reader.reads[1] == (PIN, at(6, 0), at(7, 0))
    assert len(found) == 7


def test_departures_new_day_drops_old_days() -> None:
    """Reading on a new service day evicts the earlier day's legs."""
    reader = ScheduleReader()
    cache = LegCache(zone=ZONE)
    cache.departures(timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0))
    other = pin_of('c', 'd')
    cache.departures(
        timetable=reader, pin=other, start=at(7, 0, day=28),
        end=at(8, 0, day=28),
    )
    assert cache.size() == 1


def test_departures_new_version_clears() -> None:
    """A different timetable version reads afresh."""
    reader = ScheduleReader()
    cache = LegCache(zone=ZONE)
    for version in ('v1', 'v2'):
        cache.departures(
            timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0),
            version=version,
        )
    assert len(reader.reads) == 2
    assert cache.size() == 1


def test_departures_capped() -> None:
    """Past the cap, the least recently stored leg is dropped."""
    reader = ScheduleReader()
    cache = LegCache(zone=ZONE)
    for index in range(CACHE_LIMIT + 1):
        cache.departures(
            timetable=reader, pin=pin_of(f'a{index}', 'b'),
            start=at(7, 0), end=at(8, 0),
        )
    cache.departures(
        timetable=reader, pin=pin_of('a0', 'b'), start=at(7, 0), end=at(8, 0),
    )
    assert cache.size() == CACHE_LIMIT
    assert len(reader.reads) == CACHE_LIMIT + 2


def test_departures_gap_direct_beats_cached_carry_on() -> None:
    """A carry-on beaten by a direct trip found in the gap is dropped."""
    carry = replace(
        departure(departs=at(7, 55), ride=60, trip_id='carry'),
        facts=TripFacts(onto=CarryOn(route='x', headsign='y', trip_id='z')),
    )
    direct = departure(departs=at(8, 10), ride=20, trip_id='direct')
    reader = ScheduleReader(schedule=[carry, direct])
    cache = LegCache(zone=ZONE)
    first = cache.departures(
        timetable=reader, pin=PIN, start=at(7, 0), end=at(8, 0),
    )
    found = cache.departures(
        timetable=reader, pin=PIN, start=at(7, 0), end=at(9, 0),
    )
    assert trips(items=first) == ['carry']
    assert trips(items=found) == ['direct']
