"""Tests for feed selection by mode."""

from paperroute.core.feeds import (
    FeedKind,
    alert_feeds_for,
    feed_for,
    feed_kind,
    feed_label,
    feeds_near,
    position_feeds,
)
from paperroute.core.geo import MetreBox, to_metres
from paperroute.core.model import Mode


def box_around(*, lon: float, lat: float, half: float = 1000) -> MetreBox:
    """
    Make a square area around a place.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.
    half : float
        Half the side, in metres.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    x, y = to_metres(lon=lon, lat=lat)
    return (x - half, y - half, x + half, y + half)


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


def test_feeds_near_keeps_the_light_rail_line_in_the_area() -> None:
    """Around Parramatta only the Parramatta light rail feed is kept."""
    parramatta = box_around(lon=151.003, lat=-33.817)
    feeds = position_feeds(modes=[Mode.LIGHT_RAIL, Mode.BUS])
    assert feeds_near(feeds=feeds, box=parramatta) == (
        'v1/gtfs/vehiclepos/buses',
        'v1/gtfs/vehiclepos/lightrail/parramatta',
    )


def test_feeds_near_keeps_both_lines_that_meet() -> None:
    """At Central the CBD and Inner West light rail lines both stay."""
    central = box_around(lon=151.206, lat=-33.884)
    assert feeds_near(
        feeds=position_feeds(modes=[Mode.LIGHT_RAIL]), box=central,
    ) == (
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
    )


def test_feeds_near_drops_newcastle_from_a_city_wide_map() -> None:
    """A map of all of Sydney still has no Newcastle light rail."""
    sydney = box_around(lon=151.1, lat=-33.85, half=15_000)
    feeds = position_feeds(modes=[Mode.LIGHT_RAIL])
    assert 'v1/gtfs/vehiclepos/lightrail/newcastle' not in feeds_near(
        feeds=feeds, box=sydney,
    )
    assert len(feeds_near(feeds=feeds, box=sydney)) == 3
