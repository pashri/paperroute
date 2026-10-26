"""Tests for reading both legs of a connection pin."""

from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.app_connections import between_read, departures_read
from paperroute.core.alert_model import AlertPlaces, LoadBetween
from paperroute.core.connection_model import Legs, leg_pins
from paperroute.core.messages import LoadDepartures
from paperroute.core.model import Departure, Pin
from tests.core.connection_factories import WORK, WORK_CHANGE, at
from tests.core.factories import departure, pin_of, place
from tests.fakes import EmptyPlacedTimetable

START: Final[datetime] = at(7, 0)
END: Final[datetime] = at(10, 0)

type Call = tuple[Pin, datetime, datetime]


@dataclass
class RecordingTimetable(EmptyPlacedTimetable):
    """A timetable that notes each read and answers one trip per call."""

    reads: list[Call] = field(default_factory=list)
    asked: list[frozenset[str]] = field(default_factory=list)
    empty: bool = False

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Note the call and give one departure named after it.

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
            One departure whose trip ID is the call's number, or nothing
            when `empty` is set.
        """
        self.reads.append((pin, start, end))
        if self.empty:
            return []
        return [departure(departs=start, trip_id=f'call-{len(self.reads)}')]

    def alert_places(
        self, *, route_ids: Collection[str], stop_ids: Collection[str]
    ) -> AlertPlaces:
        """
        Note the stops asked about and give each a family of itself.

        Parameters
        ----------
        route_ids : Collection[str]
            Ignored.
        stop_ids : Collection[str]
            Stop IDs.

        Returns
        -------
        AlertPlaces
            Each stop with a family holding it and its platform.
        """
        self.asked.append(frozenset(stop_ids))
        return AlertPlaces(
            routes={},
            stops={stop: frozenset({stop, f'{stop}-1'}) for stop in stop_ids},
        )


def test_plain_pin_reads_once() -> None:
    """A plain pin's departures are read once, with no other legs."""
    timetable = RecordingTimetable()
    pin = pin_of('a', 'b')
    effect = LoadDepartures(pin=pin, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    assert timetable.reads == [(pin, START, END)]
    assert event.legs is None
    assert [item.trip_id for item in event.departures] == ['call-1']
    assert event.until == END


def test_connection_reads_three_legs() -> None:
    """A connection reads leg 1, leg 2 two hours longer, then direct."""
    timetable = RecordingTimetable()
    effect = LoadDepartures(pin=WORK, start=START, end=END, timetable='v1')
    event = departures_read(timetable=timetable, effect=effect)
    first, second, direct = leg_pins(pin=WORK)
    assert timetable.reads == [
        (first, START, END),
        (second, START, END + timedelta(hours=2)),
        (direct, START, END),
    ]
    assert event.pin == WORK
    assert event.until == END
    assert event.timetable == 'v1'
    assert [item.trip_id for item in event.departures] == ['call-1']
    assert event.legs is not None
    assert [item.trip_id for item in event.legs.second] == ['call-2']
    assert [item.trip_id for item in event.legs.direct] == ['call-3']


def test_unknown_change_stop_gives_empty_legs() -> None:
    """A change stop the timetable doesn't know gives empty legs."""
    gone = replace(WORK, change=replace(WORK_CHANGE, alight=place('gone')))
    timetable = RecordingTimetable(empty=True)
    effect = LoadDepartures(pin=gone, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    assert event.legs == Legs((), ())
    assert not event.departures


def test_between_read_merges_legs() -> None:
    """Each leg's ends are looked up, and one answer comes back."""
    timetable = RecordingTimetable()
    effect = LoadBetween(pin=WORK, trip_ids=frozenset(), day=START.date())
    event = between_read(timetable=timetable, effect=effect)
    assert timetable.asked == [
        frozenset({'G2145615', 'G215254'}),
        frozenset({'2145648', '214510'}),
    ]
    assert event.pin == WORK
    assert set(event.families) == {'G2145615', 'G215254', '2145648', '214510'}
    assert not event.names
