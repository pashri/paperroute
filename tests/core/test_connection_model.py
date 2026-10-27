"""Tests for connection pins: their legs and walks."""

from dataclasses import replace
from datetime import timedelta

from paperroute.core.connection_model import (
    LEG_EXTRA,
    default_allow,
    direct_leg,
    walk_metres,
    way_legs,
)
from paperroute.core.model import Pin
from tests.core.connection_factories import (
    CONSTITUTION,
    HAMMERS,
    HOLLIS,
    PARRAMATTA,
    TWO_WAY,
    WESTMEAD,
    WORK,
)
from tests.core.factories import place


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
