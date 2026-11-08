"""Tests for the domain model."""

import re
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from paperroute.city import SYDNEY_ZONE, SydneyMode
from paperroute.core.chooser import KIND_LABELS, KIND_LETTERS, KIND_ORDER
from paperroute.core.saved import TOWN_HALL
from paperroute_testkit.core.connection_factories import (
    CAMPBELLTOWN_WAY,
    TWO_WAY,
    WORK,
    WORK_CHANGE,
)
from paperroute_testkit.core.factories import pin_of, place
from paperroute_transit.core.model import (
    Pin,
    StopPlace,
    is_connection,
    pin_ref,
    same_place,
    service_epoch,
    service_seconds,
    service_time,
    ticked_ways,
    until_repeated_hop,
    until_revisit,
    way_key,
)


def test_service_epoch_is_midnight_on_normal_days() -> None:
    """On an ordinary day the service day starts at midnight."""
    assert service_epoch(day=date(2026, 10, 16), zone=SYDNEY_ZONE) == datetime(
        2026, 10, 16, tzinfo=SYDNEY_ZONE
    )


def test_service_epoch_on_daylight_saving_start() -> None:
    """When clocks go forward, noon minus 12 hours is 23:00 the day before."""
    epoch = service_epoch(day=date(2026, 10, 4), zone=SYDNEY_ZONE)
    assert epoch == datetime(2026, 10, 3, 23, tzinfo=SYDNEY_ZONE)
    assert epoch.astimezone(UTC) == datetime(2026, 10, 3, 13, tzinfo=UTC)


def test_service_time_after_daylight_saving_start() -> None:
    """A GTFS 08:00:00 on the change day is 8 am on the new clock."""
    eight = service_time(
        day=date(2026, 10, 4),
        seconds=8 * 3600,
        zone=SYDNEY_ZONE,
    )
    assert eight.astimezone(UTC) == datetime(2026, 10, 3, 21, tzinfo=UTC)
    assert (eight.hour, eight.minute) == (8, 0)


def test_service_seconds_inverts_service_time() -> None:
    """Seconds since the epoch round-trip through a Sydney time."""
    moment = service_time(
        day=date(2026, 10, 4),
        seconds=90600,
        zone=SYDNEY_ZONE,
    )
    assert service_seconds(
        day=date(2026, 10, 4),
        moment=moment,
        zone=SYDNEY_ZONE,
    ) == 90600


def test_service_time_before_daylight_saving_start() -> None:
    """A GTFS 01:00:00 on the change day is before the clocks jump."""
    one = service_time(day=date(2026, 10, 4), seconds=3600, zone=SYDNEY_ZONE)
    assert one.astimezone(UTC) == datetime(2026, 10, 3, 14, tzinfo=UTC)


def test_until_revisit_cuts_before_a_repeat() -> None:
    """The run stops before the first station seen twice."""
    assert until_revisit(stations=['A', 'B', 'C', 'B', 'D']) == (
        'A', 'B', 'C',
    )
    assert until_revisit(stations=['A', 'B']) == ('A', 'B')


def test_until_repeated_hop_keeps_a_loop() -> None:
    """A loop through a station twice stays whole until a hop repeats."""
    loop = ('C', 'L1', 'L2', 'J', 'C', 'E1')
    assert until_repeated_hop(stations=loop) == loop
    assert until_repeated_hop(stations=['A', 'B', 'C', 'A', 'B', 'D']) == (
        'A', 'B', 'C', 'A',
    )


def test_same_place_ignores_names_and_points() -> None:
    """Pins match on stop IDs, kind and routes, not names or points."""
    bare = Pin(origin=place('A'), destination=place('B'), routes=('606',))
    named = Pin(
        origin=place('A', name='Hollis St', point=(1.0, 2.0)),
        destination=place('B', name='Parramatta'),
        routes=('606',),
        missing=True,
    )
    assert same_place(first=bare, second=named)


@pytest.mark.parametrize('other', [
    pin_of('A', None),
    pin_of('A', 'C'),
    pin_of('C', 'B'),
    Pin(origin=place('A'), destination=place('B'), kind=SydneyMode.BUS),
])
def test_same_place_tells_pins_apart(other: Pin) -> None:
    """A different stop, kind or route list is a different pin."""
    assert not same_place(first=pin_of('A', 'B'), second=other)


def test_kind_order_is_bus_train_metro_lr_ferry_school_coach() -> None:
    """The chooser lists kinds in its fixed order, with Coach last."""
    assert KIND_ORDER == (
        SydneyMode.BUS, SydneyMode.TRAIN, SydneyMode.METRO,
        SydneyMode.LIGHT_RAIL, SydneyMode.FERRY, SydneyMode.SCHOOL,
        SydneyMode.COACH,
    )
    assert KIND_LABELS[SydneyMode.COACH] == 'Coach'
    assert KIND_LETTERS[SydneyMode.COACH] == 'C'


def test_pin_key_includes_filter_not_nickname() -> None:
    """The route filter makes a different place; a nickname does not."""
    base = Pin(origin=place('A'), destination=place('B'))
    base = Pin(origin=place('A'))
    filtered = replace(base, only=('606',))
    named = replace(base, nickname='Work')
    assert not same_place(first=base, second=filtered)
    assert same_place(first=base, second=named)


def test_pin_ref_is_stable_and_short() -> None:
    """A ref is twelve hex digits that change with the filter."""
    pin = Pin(origin=TOWN_HALL, kind=SydneyMode.TRAIN, routes=('T1',))
    ref = pin_ref(pin=pin)
    assert re.fullmatch('[0-9a-f]{12}', ref)
    assert ref == pin_ref(pin=pin)
    assert ref != pin_ref(pin=replace(pin, only=('T2',)))


def test_pin_ref_unchanged() -> None:
    """A pin's ref is the one it had when kinds were an enum."""
    pin = Pin(
        origin=StopPlace(stop_id='200060', name='Central', point=None),
        kind='bus',
        routes=('606',),
    )
    assert pin_ref(pin=pin) == '8a5e960f4c4b'


def test_way_key_ignores_allow_and_tick() -> None:
    """A way is named by where it changes, not its allow or tick."""
    way = WORK.ways[0]
    longer = replace(way, changes=(replace(WORK_CHANGE, allow=9),))
    unticked = replace(way, ticked=False)
    assert way_key(way=way) == (('G215254', '2145648'),)
    assert way_key(way=longer) == way_key(way=way) == way_key(way=unticked)
    assert way_key(way=TWO_WAY) != way_key(way=way)


def test_identity_ignores_routes_with_destination() -> None:
    """Routes make a place only when there is no Destination."""
    base = Pin(origin=place('A'), destination=place('B'))
    routed = replace(base, routes=('606',), only=('606',))
    assert same_place(first=base, second=routed)
    assert pin_ref(pin=base) == pin_ref(pin=routed)
    open_ended = Pin(origin=place('A'))
    assert not same_place(
        first=open_ended, second=replace(open_ended, routes=('606',)),
    )


def test_identity_ignores_ways() -> None:
    """Editing ways keeps a pin's identity and ref."""
    plain = replace(WORK, ways=())
    longer = replace(WORK, ways=(replace(WORK.ways[0], ticked=False),))
    assert same_place(first=WORK, second=plain)
    assert same_place(first=WORK, second=longer)
    assert pin_ref(pin=WORK) == pin_ref(pin=plain)


def test_never_not_in_identity() -> None:
    """The never list, like the ways, leaves the pin and its ref the same."""
    base = Pin(origin=place('A'), destination=place('B'))
    never = replace(base, never=('2', '1'))
    assert same_place(first=base, second=never)
    assert pin_ref(pin=base) == pin_ref(pin=never)
    open_ended = Pin(origin=TOWN_HALL, kind=SydneyMode.TRAIN, routes=('T1',))
    assert pin_ref(pin=open_ended) == pin_ref(
        pin=replace(open_ended, never=('T2',)),
    )


def test_ticked_ways_and_is_connection() -> None:
    """Only ticked ways are listed; any way makes a connection."""
    off = replace(CAMPBELLTOWN_WAY, ticked=False)
    pin = replace(WORK, ways=(WORK.ways[0], off))
    assert ticked_ways(pin=pin) == (WORK.ways[0],)
    assert is_connection(pin=replace(WORK, ways=(off,)))
    assert not is_connection(pin=replace(WORK, ways=()))
