"""Tests for feed selection by mode."""

from paperroute.core.feeds import feed_for
from paperroute.core.model import Mode


def test_feed_for_live_modes() -> None:
    """Buses, trains, metro and NSW TrainLink have live feeds in M1."""
    assert feed_for(mode=Mode.BUS) == 'v1/gtfs/realtime/buses'
    assert feed_for(mode=Mode.TRAIN) == 'v2/gtfs/realtime/sydneytrains'
    assert feed_for(mode=Mode.METRO) == 'v2/gtfs/realtime/metro'
    assert feed_for(mode=Mode.REGIONAL) == 'v1/gtfs/realtime/nswtrains'


def test_feed_for_other_modes_is_none() -> None:
    """Ferries and the rest are timetable-only in M1."""
    assert feed_for(mode=Mode.FERRY) is None
