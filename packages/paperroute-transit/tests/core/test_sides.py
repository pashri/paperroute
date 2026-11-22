"""Tests for the kerb pair."""

from dataclasses import FrozenInstanceError, replace
from typing import Final

import pytest

from paperroute_testkit.core.factories import place
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.sides import (
    KerbPair,
    both_sides,
    kept_sides,
    pin_places,
    pin_title,
)


def test_kerb_pair_is_frozen() -> None:
    """A pair cannot be changed once read."""
    pair = KerbPair(station='B1', other=place('B2'), busy=True)
    assert pair.other.stop_id == 'B2'
    with pytest.raises(FrozenInstanceError):
        setattr(pair, 'busy', False)


FROM: Final[StopPlace] = StopPlace(
    stop_id='s2', name='Orchard Close', point=(0.0, 0.0),
)
ACROSS: Final[StopPlace] = StopPlace(
    stop_id='x2', name='Orchard Close', point=(0.0, 40.0),
)
TO: Final[StopPlace] = StopPlace(
    stop_id='s4', name='Emmanuel Street', point=None,
)
BOTH: Final[Pin] = Pin(
    origin=FROM, kind='bus', opposite=ACROSS,
    towards=('Emmanuel Street', 'Oakington'),
)


def test_both_sides_needs_no_destination() -> None:
    """A pin is both-sides only with an opposite kerb and no Destination."""
    assert both_sides(pin=BOTH)
    assert not both_sides(pin=replace(BOTH, destination=TO))
    assert not both_sides(pin=replace(BOTH, opposite=None))


def test_pin_places_lists_the_kerb_across_only_for_both_sides() -> None:
    """The kerb across is read only while the pin has no Destination."""
    assert pin_places(pin=BOTH) == (FROM, ACROSS)
    assert pin_places(pin=replace(BOTH, destination=TO)) == (FROM, TO)


def test_pin_title_names_both_sides() -> None:
    """A both-sides pin's title says so."""
    assert pin_title(pin=BOTH) == 'Orchard Close, both sides'
    assert pin_title(pin=replace(BOTH, opposite=None)) == 'Orchard Close'


def test_kept_sides_keeps_the_opposite_kerb_on_an_edit() -> None:
    """An edit keeping the From and no Destination keeps the kerb across."""
    edited = replace(BOTH, opposite=None, towards=(), nickname='Home')
    kept = kept_sides(old=BOTH, new=edited)
    assert (kept.opposite, kept.towards) == (BOTH.opposite, BOTH.towards)
    moved = replace(edited, origin=TO)
    assert kept_sides(old=BOTH, new=moved).opposite is None
    routed = replace(edited, destination=TO)
    assert kept_sides(old=BOTH, new=routed).opposite is None
    assert kept_sides(old=None, new=edited) == edited
