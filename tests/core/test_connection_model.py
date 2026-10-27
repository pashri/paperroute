"""Tests for connection pins and their pairing."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute.core.connection_model import (
    LEG_EXTRA,
    Legs,
    Pair,
    all_departures,
    default_allow,
    direct_leg,
    pairs_of,
    walk_metres,
    way_legs,
)
from paperroute.core.live import LiveDeparture
from paperroute.core.model import Departure, Pin
from paperroute.core.stop_view import clock_text
from tests.core.connection_factories import (
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    LEG1,
    LEG2,
    PARRAMATTA,
    TWO_WAY,
    WESTMEAD,
    WORK,
    at,
    run,
)
from tests.core.factories import departure, live, place


def lived(items: Sequence[Departure]) -> tuple[LiveDeparture, ...]:
    """
    Wrap departures as on time.

    Parameters
    ----------
    items : Sequence[Departure]
        Departures.

    Returns
    -------
    tuple[LiveDeparture, ...]
        Live departures with no delay.
    """
    return tuple(live(item=item) for item in items)


def test_pairs_match_real_times() -> None:
    """The real Tuesday times pair as the timetable study found."""
    pairs = pairs_of(
        first=lived(LEG1), second=lived(LEG2), direct=(), allow=7,
        now=at(7, 30),
    )
    assert len(pairs) == 5
    assert [
        None if p.second is None else p.second.departure.route
        for p in pairs
    ] == ['665', '664', '665', '665', '665']
    assert [clock_text(moment=p.arrives) for p in pairs] == [
        '7:57', '8:12', '8:27', '8:45', '9:04',
    ]


def test_earliest_arrival_not_earliest_departure() -> None:
    """The 7:33 takes the 665 arriving first, not the 662 leaving first."""
    pairs = pairs_of(
        first=lived(LEG1[:1]), second=lived(LEG2), direct=(), allow=7,
        now=at(7, 30),
    )
    assert pairs[0].second is not None
    assert pairs[0].second.departure.trip_id == 'b0746'


def test_delay_repairs_silently() -> None:
    """A late leg 1 pairs with the next catchable leg 2; ties load first."""
    pairs = pairs_of(
        first=(live(item=LEG1[0], late=6),), second=lived(LEG2), direct=(),
        allow=7, now=at(7, 30),
    )
    assert pairs[0].second is not None
    assert pairs[0].second.departure.trip_id == 'b0801a'
    assert clock_text(moment=pairs[0].arrives) == '8:12'


def test_cancelled_second_leg_skipped() -> None:
    """A cancelled leg 2 is passed over."""
    second = (live(item=LEG2[0], cancelled=True), *lived(LEG2[1:]))
    pairs = pairs_of(
        first=lived(LEG1[:1]), second=second, direct=(), allow=7,
        now=at(7, 30),
    )
    assert pairs[0].second is not None
    assert pairs[0].second.departure.route == '662'
    assert clock_text(moment=pairs[0].arrives) == '8:02'


def test_no_workable_second_leg_hidden() -> None:
    """A leg 1 with no catchable leg 2 is hidden."""
    pairs = pairs_of(
        first=lived(LEG1), second=lived(LEG2[:1]), direct=(), allow=7,
        now=at(7, 30),
    )
    assert [p.first.departure.trip_id for p in pairs] == ['a0733']


@pytest.mark.parametrize(('arrives', 'stays'), [
    ((7, 55), True),
    ((7, 57), True),
    ((8, 10), False),
])
def test_staying_on_wins_when_faster(
    arrives: tuple[int, int], stays: bool,
) -> None:
    """Staying on competes with changing, and wins ties."""
    twin = live(
        item=run(
            departs=(7, 33), arrives=arrives, route='606', trip_id='a0733',
        ),
    )
    pairs = pairs_of(
        first=lived(LEG1[:1]), second=lived(LEG2), direct=(twin,),
        allow=7, now=at(7, 30),
    )
    expected = (
        Pair(first=twin, second=None)
        if stays
        else Pair(first=live(item=LEG1[0]), second=live(item=LEG2[0]))
    )
    assert pairs == (expected,)


def test_direct_without_twin_is_stay_row() -> None:
    """A direct trip with no leg-1 twin is a stay row in order."""
    lone = live(
        item=run(departs=(7, 40), arrives=(8, 0), route='606', trip_id='z'),
    )
    pairs = pairs_of(
        first=lived(LEG1[:2]), second=lived(LEG2), direct=(lone,), allow=7,
        now=at(7, 30),
    )
    assert pairs[1] == Pair(first=lone, second=None)
    assert [p.first.departure.trip_id for p in pairs] == [
        'a0733', 'z', 'a0748',
    ]


def test_departed_leg_one_dropped() -> None:
    """A pair whose leg 1 has left is dropped."""
    pairs = pairs_of(
        first=lived(LEG1), second=lived(LEG2), direct=(), allow=7,
        now=at(7, 34),
    )
    assert pairs[0].first.departure.trip_id == 'a0748'


def test_cancelled_leg_one_dropped() -> None:
    """A pair whose leg 1 is cancelled is dropped."""
    first = (live(item=LEG1[0], cancelled=True), *lived(LEG1[1:]))
    pairs = pairs_of(
        first=first, second=lived(LEG2), direct=(), allow=7, now=at(7, 30),
    )
    assert pairs[0].first.departure.trip_id == 'a0748'


def test_pairs_across_midnight() -> None:
    """A leg 2 on the next service day still pairs."""
    first = departure(departs=at(23, 50), ride=6, trip_id='late')
    second = departure(departs=at(0, 10, day=28), ride=10, trip_id='night')
    pairs = pairs_of(
        first=(live(item=first),), second=(live(item=second),), direct=(),
        allow=7, now=at(23, 40),
    )
    assert pairs == (Pair(first=live(item=first), second=live(item=second)),)
    assert second.service_day.day == 28


def test_pair_arrives_falls_back_to_first() -> None:
    """A stay pair arrives when its first leg does."""
    item = live(item=LEG1[0])
    assert Pair(first=item, second=None).arrives == item.arrives


def test_walk_and_allow() -> None:
    """The walk rounds up to 10 m and the allow follows it."""
    assert walk_metres(alight=HAMMERS, board=CONSTITUTION) == 150
    assert default_allow(metres=150) == 4
    far = place('F', point=(0.0, 367.0))
    assert walk_metres(alight=HAMMERS, board=far) == 370
    assert default_allow(metres=370) == 7
    assert walk_metres(alight=HAMMERS, board=place('N')) == 0
    assert default_allow(metres=0) == 2


def test_way_legs_two_changes() -> None:
    """Two changes give three chained leg pins of any kind and route."""
    pin = replace(WORK, destination=PARRAMATTA)
    first, middle, last = way_legs(pin=pin, way=TWO_WAY)
    assert all(leg.kind is None and leg.routes == () for leg in (
        first, middle, last,
    ))
    assert (first.origin, first.destination) == (HOLLIS, HAMMERS)
    assert (middle.origin, middle.destination) == (CONSTITUTION, WESTMEAD)
    assert (last.origin, last.destination) == (WESTMEAD, PARRAMATTA)


def test_way_legs_one_change() -> None:
    """One change gives two legs ending at the Destination."""
    first, second = way_legs(pin=WORK, way=WORK.ways[0])
    assert first == Pin(origin=HOLLIS, destination=HAMMERS, kind=None)
    assert second == Pin(
        origin=CONSTITUTION, destination=WESTMEAD, kind=None,
    )


def test_direct_leg() -> None:
    """The direct leg runs From to the Destination on any route."""
    assert direct_leg(pin=WORK) == Pin(
        origin=HOLLIS, destination=WESTMEAD, kind=None,
    )


def test_all_departures_in_leg_order() -> None:
    """Leg 1 comes first, then leg 2, then the direct list."""
    legs = Legs(second=LEG2[:1], direct=LEG1[1:2])
    assert all_departures(departures=LEG1[:1], legs=legs) == (
        LEG1[0], LEG2[0], LEG1[1],
    )
    assert all_departures(departures=LEG1[:1], legs=None) == LEG1[:1]


def test_leg_two_extra_is_two_hours() -> None:
    """Leg 2 is searched two hours past the window."""
    assert LEG_EXTRA == timedelta(hours=2)


def test_change_exactly_allow_is_catchable() -> None:
    """A leg 2 leaving exactly `allow` minutes after arrival is caught."""
    pairs = pairs_of(
        first=(live(item=LEG1[0], late=5),), second=lived(LEG2), direct=(),
        allow=7, now=at(7, 30),
    )
    assert pairs[0].second is not None
    assert pairs[0].second.departure.trip_id == 'b0751'


def test_shared_second_leg_keeps_latest_first() -> None:
    """Of the leg 1s that catch one leg 2, only the last to leave stays."""
    pairs = pairs_of(
        first=lived(LEG1), second=lived(LEG2[-2:]), direct=(), allow=7,
        now=at(7, 30),
    )
    assert [
        (p.first.departure.trip_id, p.second and p.second.departure.trip_id)
        for p in pairs
    ] == [('a0842', 'b0855')]
