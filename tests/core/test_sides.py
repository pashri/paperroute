"""Tests for pins that cover both kerbs of a road."""

from paperroute_testkit.core.factories import place
from paperroute_transit.core.model import Pin
from paperroute_transit.core.sides import both_sides


def test_both_sides_is_false_when_the_kerb_across_is_the_from() -> None:
    """An opposite equal to the From is not a second kerb."""
    assert not both_sides(pin=Pin(origin=place('s2'), opposite=place('s2')))
    assert both_sides(pin=Pin(origin=place('s2'), opposite=place('x2')))
