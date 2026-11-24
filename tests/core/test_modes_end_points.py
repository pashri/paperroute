"""Tests for the modes chosen by end points."""

from dataclasses import replace

from paperroute_testkit.city_setting import (
    city_under_test,
    line_mode,
    train_mode,
)
from paperroute_transit.core.model import Pin, StopPlace, pin_ref, pin_routes
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.strip_state import strip_key

PIN: Pin = Pin(
    origin=StopPlace(stop_id='a', name='A Stop', point=None),
)


def test_end_point_modes_are_the_city_trains() -> None:
    """The city's train, and nothing else, takes end points."""
    assert end_point_modes(modes=city_under_test().modes) == {
        train_mode(),
    }


def test_line_mode_is_not_an_end_point_mode() -> None:
    """The line mode is chosen by line."""
    assert line_mode() not in end_point_modes(modes=city_under_test().modes)


def test_a_stored_train_route_is_ignored_everywhere() -> None:
    """An old train pin's routes reach no reader."""
    pin = replace(PIN, kind=train_mode(), routes=('T1',))
    ends = frozenset({train_mode()})
    assert not pin_routes(pin=pin, end_points=ends)
    assert not strip_key(pin=pin, end_points=ends).routes


def test_a_line_pin_keeps_its_routes() -> None:
    """A pin of a mode chosen by line reads its routes."""
    pin = replace(PIN, kind=line_mode(), routes=('M1',))
    ends = frozenset({train_mode()})
    assert pin_routes(pin=pin, end_points=ends) == ('M1',)


def test_a_stored_train_pin_keeps_its_ref() -> None:
    """Identity still counts the stored routes."""
    pin = replace(PIN, kind=train_mode(), routes=('T1',))
    assert pin_ref(pin=pin) != pin_ref(pin=replace(pin, routes=()))
