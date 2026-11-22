"""Tests for the kerb pair."""

from dataclasses import FrozenInstanceError

import pytest

from paperroute_testkit.core.factories import place
from paperroute_transit.core.sides import KerbPair


def test_kerb_pair_is_frozen() -> None:
    """A pair cannot be changed once read."""
    pair = KerbPair(station='B1', other=place('B2'), busy=True)
    assert pair.other.stop_id == 'B2'
    with pytest.raises(FrozenInstanceError):
        setattr(pair, 'busy', False)
