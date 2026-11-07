"""Tests for reading both legs of a connection pin."""

from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Final

from paperroute.app_connections import between_read, departures_read
from paperroute.core.chains import leg_key
from paperroute.core.connection_model import direct_leg, way_legs
from paperroute.core.messages import LoadDepartures
from paperroute_testkit.core.connection_factories import (
    CAMPBELLTOWN_WAY,
    PARRAMATTA,
    TWO_WAY,
    WORK,
    WORK_CHANGE,
    at,
)
from paperroute_testkit.core.factories import departure, pin_of, place
from paperroute_testkit.fakes import EmptyPlacedTimetable
from paperroute_transit.adapters.leg_cache import LegCache
from paperroute_transit.core.alert_model import AlertPlaces, LoadBetween
from paperroute_transit.core.model import Departure, Pin, RouteKind, Way

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

    def leg_lines(
        self, *, ends: tuple[str, str], day: date,
    ) -> frozenset[str]:
        """
        Name one route ID after the leg's ends.

        Parameters
        ----------
        ends : tuple[str, str]
            From and To stop IDs.
        day : date
            Ignored.

        Returns
        -------
        frozenset[str]
            One route ID made of the two stop IDs.
        """
        return frozenset({'>'.join(ends)})


@dataclass
class RoutesTimetable(RecordingTimetable):
    """A timetable that answers one departure per route on each call."""

    routes: tuple[str, ...] = ()
    kinds: dict[str, RouteKind] = field(default_factory=dict)

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Note the call and give one departure on each route.

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
            One departure per route.
        """
        self.reads.append((pin, start, end))
        return [
            departure(
                departs=start,
                route=route,
                trip_id=route,
                kind=self.kinds.get(route),
            )
            for route in self.routes
        ]


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


def test_connection_reads_each_leg() -> None:
    """A connection reads direct, then each leg, later legs for longer."""
    timetable = RecordingTimetable()
    effect = LoadDepartures(pin=WORK, start=START, end=END, timetable='v1')
    event = departures_read(timetable=timetable, effect=effect)
    first, second = way_legs(pin=WORK, way=WORK.ways[0])
    assert timetable.reads == [
        (direct_leg(pin=WORK), START, END),
        (first, START, END),
        (second, START, END + timedelta(hours=2)),
    ]
    assert event.pin == WORK
    assert event.until == END
    assert event.timetable == 'v1'
    assert [item.trip_id for item in event.departures] == ['call-1']
    assert event.legs is not None
    assert {
        key: [item.trip_id for item in items]
        for key, items in event.legs.lists.items()
    } == {
        leg_key(leg=first): ['call-2'],
        leg_key(leg=second): ['call-3'],
    }


def test_shared_leg_loaded_once() -> None:
    """Two ways starting From -> Hammers read that leg once."""
    pin = replace(WORK, ways=(*WORK.ways, CAMPBELLTOWN_WAY))
    timetable = RecordingTimetable()
    effect = LoadDepartures(pin=pin, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    shared = way_legs(pin=pin, way=WORK.ways[0])[0]
    assert [read[0] for read in timetable.reads].count(shared) == 1
    assert len(timetable.reads) == 4
    assert event.legs is not None
    assert len(event.legs.lists) == 3


def test_second_change_window_extended() -> None:
    """The leg after a second change is read four hours longer."""
    pin = replace(WORK, ways=(TWO_WAY,))
    timetable = RecordingTimetable()
    effect = LoadDepartures(pin=pin, start=START, end=END)
    departures_read(timetable=timetable, effect=effect)
    last = way_legs(pin=pin, way=TWO_WAY)[2]
    assert (last, START, END + timedelta(hours=4)) in timetable.reads


def test_unticked_way_not_read() -> None:
    """Only ticked ways' legs are read."""
    pin = replace(
        WORK, ways=(*WORK.ways, replace(CAMPBELLTOWN_WAY, ticked=False)),
    )
    timetable = RecordingTimetable()
    effect = LoadDepartures(pin=pin, start=START, end=END)
    departures_read(timetable=timetable, effect=effect)
    assert len(timetable.reads) == 3


def test_plain_destination_pin_any_route() -> None:
    """A plain pin with a To reads any route there, less its never."""
    pin = Pin(
        origin=WORK.origin,
        destination=WORK.destination,
        kind=RouteKind.BUS,
        never=('664',),
    )
    timetable = RoutesTimetable(routes=('711', '609', '664'))
    effect = LoadDepartures(pin=pin, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    assert timetable.reads == [(direct_leg(pin=pin), START, END)]
    assert [item.route for item in event.departures] == ['711', '609']
    assert event.legs is None


def test_never_dropped_from_every_leg() -> None:
    """A never route is dropped from the way legs too."""
    pin = replace(WORK, never=('711',))
    timetable = RoutesTimetable(routes=('711', '665'))
    effect = LoadDepartures(pin=pin, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    assert event.legs is not None
    assert {
        item.route for items in event.legs.lists.values() for item in items
    } == {'665'}


def test_school_routes_dropped_when_asked() -> None:
    """School routes leave direct trips and legs, and the load says so."""
    timetable = RoutesTimetable(
        routes=('711', 'S1'), kinds={'S1': RouteKind.SCHOOL},
    )
    effect = LoadDepartures(pin=WORK, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    assert event.skip_school
    assert [item.route for item in event.departures] == ['711']
    assert event.legs is not None
    assert {
        item.route for items in event.legs.lists.values() for item in items
    } == {'711'}


def test_school_routes_dropped_on_pin_without_to_when_asked() -> None:
    """A pin without a To drops school routes only when the load asks."""
    timetable = RoutesTimetable(
        routes=('711', 'S1'), kinds={'S1': RouteKind.SCHOOL},
    )
    pin = replace(WORK, destination=None)
    plain = LoadDepartures(pin=pin, start=START, end=END)
    asked = replace(plain, skip_school_pins=True)
    kept = departures_read(timetable=timetable, effect=plain)
    dropped = departures_read(timetable=timetable, effect=asked)
    assert [item.route for item in kept.departures] == ['711', 'S1']
    assert [item.route for item in dropped.departures] == ['711']
    assert dropped.skip_school_pins


def test_school_routes_kept_when_not_skipped() -> None:
    """With school buses allowed, school routes stay."""
    timetable = RoutesTimetable(
        routes=('711', 'S1'), kinds={'S1': RouteKind.SCHOOL},
    )
    effect = LoadDepartures(
        pin=WORK, start=START, end=END, skip_school=False,
    )
    event = departures_read(timetable=timetable, effect=effect)
    assert not event.skip_school
    assert [item.route for item in event.departures] == ['711', 'S1']


def test_unknown_change_stop_gives_empty_list() -> None:
    """A change stop the timetable doesn't know gives empty lists."""
    gone = replace(
        WORK,
        ways=(Way(changes=(replace(WORK_CHANGE, alight=place('gone')),)),),
    )
    timetable = RecordingTimetable(empty=True)
    effect = LoadDepartures(pin=gone, start=START, end=END)
    event = departures_read(timetable=timetable, effect=effect)
    assert event.legs is not None
    assert all(not items for items in event.legs.lists.values())
    assert len(event.legs.lists) == 2
    assert not event.departures


def test_between_read_merges_legs() -> None:
    """Each leg's ends are looked up, and one answer comes back."""
    timetable = RecordingTimetable()
    effect = LoadBetween(pin=WORK, trip_ids=frozenset(), day=START.date())
    event = between_read(timetable=timetable, effect=effect)
    assert timetable.asked == [
        frozenset({'G2145615', '214510'}),
        frozenset({'G2145615', 'G215254'}),
        frozenset({'2145648', '214510'}),
    ]
    assert event.pin == WORK
    assert set(event.families) == {'G2145615', 'G215254', '2145648', '214510'}
    assert not event.names
    assert event.lines == {
        'G2145615>214510', 'G2145615>G215254', '2145648>214510',
    }


def test_between_read_covers_two_changes() -> None:
    """Each leg of a two-change way, and direct, is looked up."""
    pin = replace(WORK, destination=PARRAMATTA, ways=(TWO_WAY,))
    timetable = RecordingTimetable()
    effect = LoadBetween(pin=pin, trip_ids=frozenset(), day=START.date())
    event = between_read(timetable=timetable, effect=effect)
    assert timetable.asked == [
        frozenset({'G2145615', '2150'}),
        frozenset({'G2145615', 'G215254'}),
        frozenset({'2145648', '214510'}),
        frozenset({'214510', '2150'}),
    ]
    assert set(event.families) == {
        'G2145615', 'G215254', '2145648', '214510', '2150',
    }


def test_connection_second_read_cached() -> None:
    """With a cache, a repeat load of a connection reads nothing."""
    timetable = RecordingTimetable()
    cache = LegCache()
    effect = LoadDepartures(pin=WORK, start=START, end=END, timetable='v1')
    first = departures_read(timetable=timetable, effect=effect, cache=cache)
    second = departures_read(timetable=timetable, effect=effect, cache=cache)
    assert len(timetable.reads) == 3
    assert second == first
