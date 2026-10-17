"""Tests for feed selection by mode."""

from paperroute.core.feeds import FeedKind, feed_for, feed_kind, position_feeds
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


def test_position_feeds_for_modes() -> None:
    """Position feeds are sorted and unique; COACH has none."""
    assert position_feeds(modes=[Mode.TRAIN, Mode.BUS, Mode.BUS]) == (
        'v1/gtfs/vehiclepos/buses',
        'v2/gtfs/vehiclepos/sydneytrains',
    )
    assert position_feeds(modes=[Mode.COACH]) == ()
    assert len(position_feeds(modes=[Mode.LIGHT_RAIL])) == 4


def test_feed_kind() -> None:
    """Only paths with /vehiclepos/ are position feeds."""
    assert feed_kind(feed='v1/gtfs/vehiclepos/buses') == FeedKind.POSITIONS
    assert feed_kind(feed='v1/gtfs/realtime/buses') == FeedKind.TRIPS
