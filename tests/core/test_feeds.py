"""Tests for feed selection by mode."""

from paperroute.core.feeds import (
    FeedKind,
    alert_feeds_for,
    feed_for,
    feed_kind,
    feed_label,
    position_feeds,
)
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
    assert position_feeds(modes=[Mode.LIGHT_RAIL]) == (
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v1/gtfs/vehiclepos/lightrail/newcastle',
        'v1/gtfs/vehiclepos/lightrail/parramatta',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
    )
    assert position_feeds(
        modes=[Mode.METRO, Mode.REGIONAL, Mode.FERRY]
    ) == (
        'v1/gtfs/vehiclepos/ferries/sydneyferries',
        'v1/gtfs/vehiclepos/nswtrains',
        'v2/gtfs/vehiclepos/metro',
    )


def test_feed_kind() -> None:
    """Only paths with /vehiclepos/ are position feeds."""
    assert feed_kind(feed='v1/gtfs/vehiclepos/buses') == FeedKind.POSITIONS
    assert feed_kind(feed='v1/gtfs/realtime/buses') == FeedKind.TRIPS


def test_feed_label() -> None:
    """Feed paths get names a person reads."""
    assert feed_label(feed='v1/gtfs/realtime/buses') == 'Buses trip updates'
    assert feed_label(feed='v2/gtfs/realtime/sydneytrains') == (
        'Sydney Trains trip updates'
    )
    assert feed_label(feed='v2/gtfs/vehiclepos/metro') == 'Metro positions'
    assert feed_label(feed='v1/gtfs/vehiclepos/ferries/sydneyferries') == (
        'Ferries positions'
    )
    assert feed_label(
        feed='v2/gtfs/vehiclepos/lightrail/innerwest',
    ) == 'Light rail positions (innerwest)'
    assert feed_label(feed='v9/other') == 'v9/other'


def test_alert_feeds_for_modes() -> None:
    """Alert feeds are sorted and unique."""
    assert alert_feeds_for(modes=[Mode.TRAIN, Mode.BUS, Mode.BUS]) == (
        'v2/gtfs/alerts/buses',
        'v2/gtfs/alerts/sydneytrains',
    )
    assert alert_feeds_for(modes=[Mode.FERRY]) == (
        'v2/gtfs/alerts/ferries',
    )


def test_feed_kind_alerts() -> None:
    """Paths containing /alerts/ are alert feeds."""
    assert feed_kind(feed='v2/gtfs/alerts/metro') == FeedKind.ALERTS
    assert FeedKind.ALERTS.value == 'alerts'


def test_feed_label_alerts() -> None:
    """Alert feeds are named after their mode."""
    assert feed_label(feed='v2/gtfs/alerts/lightrail') == 'Light rail alerts'
    assert feed_label(feed='v2/gtfs/alerts/ferries') == 'Ferries alerts'
    assert feed_label(feed='v2/gtfs/alerts/sydneytrains') == (
        'Sydney Trains alerts'
    )
