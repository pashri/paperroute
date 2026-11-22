"""Tests for the kerb across the road on a both-sides pin's Map."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import place
from paperroute_testkit.core.strip_factories import CENTRE, PIN
from paperroute_transit.core.map_sides import kerb_across, with_across
from paperroute_transit.core.map_view import TripMarks
from paperroute_transit.core.model import Pin, StopPlace

ACROSS: Final[StopPlace] = place(
    'x2', name='Opposite', point=(CENTRE[0], CENTRE[1] + 37.0),
)
BOTH: Final[Pin] = replace(PIN, destination=None, opposite=ACROSS)
FROM: Final[TripMarks] = TripMarks(
    origin=CENTRE, second=(), walk=None, change=None, end=None,
)


def test_kerb_across_is_the_opposite_kerb_of_a_both_sides_pin() -> None:
    """Only a pin with an opposite kerb and no Destination has one."""
    assert kerb_across(pin=BOTH) == ACROSS
    assert kerb_across(pin=replace(BOTH, destination=PIN.destination)) is None
    assert kerb_across(pin=replace(PIN, destination=None)) is None


def test_with_across_joins_the_kerb_to_the_from() -> None:
    """The marks take the kerb's point; no point or no marks: unchanged."""
    assert with_across(trip=FROM, across=ACROSS) == replace(
        FROM, across=ACROSS.point,
    )
    assert with_across(trip=FROM, across=None) == FROM
    assert with_across(
        trip=FROM, across=replace(ACROSS, point=None),
    ) == FROM
    assert with_across(trip=None, across=ACROSS) is None
