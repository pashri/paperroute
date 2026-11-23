"""Tests for connection pins: their legs and walks."""

from dataclasses import replace
from datetime import timedelta

from paperroute_testkit.core.connection_factories import (
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    PARRAMATTA,
    TWO_WAY,
    WESTMEAD,
    WORK,
    WORK_CHANGE,
    at,
)
from paperroute_testkit.core.factories import place
from paperroute_transit.core.connection_model import (
    LEG_EXTRA,
    default_allow,
    direct_leg,
    spare_seconds,
    walk_metres,
    walk_minutes,
    way_legs,
)
from paperroute_transit.core.model import Change, Pin


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


def test_leg_two_extra_is_two_hours() -> None:
    """Leg 2 is searched two hours past the window."""
    assert LEG_EXTRA == timedelta(hours=2)


def test_walk_minutes_round_up_at_the_usual_pace() -> None:
    """150 m is two minutes; a change at one stop walks none."""
    assert walk_minutes(change=WORK_CHANGE) == 2
    same = Change(alight=WESTMEAD, board=WESTMEAD, allow=4)
    assert walk_minutes(change=same) == 0


def test_spare_seconds_take_the_walk_off() -> None:
    """Spare is the gap less the walk; it goes negative when missed."""
    assert spare_seconds(
        arrives=at(7, 43), departs=at(7, 46), change=WORK_CHANGE,
    ) == 60.0
    assert spare_seconds(
        arrives=at(7, 45), departs=at(7, 46), change=WORK_CHANGE,
    ) == -60.0
