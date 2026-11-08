"""Tests for Sydney's realtime feeds."""

from datetime import datetime
from pathlib import Path
from typing import Final

from paperroute.city import SYDNEY, SYDNEY_PROJECTION, SYDNEY_ZONE, SydneyFeed
from paperroute.sydney_feeds import SydneyFeeds
from paperroute_transit.adapters.gtfs_rt import parse_positions
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import MetreBox, to_metres

REAL: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'
AT: Final[datetime] = datetime(2026, 10, 21, 8, tzinfo=SYDNEY_ZONE)
PLAN: Final[SydneyFeeds] = SydneyFeeds(projection=SYDNEY_PROJECTION)
TRIP_FEEDS: Final[dict[str, str]] = {
    'bus': 'v1/gtfs/realtime/buses',
    'train': 'v2/gtfs/realtime/sydneytrains',
    'metro': 'v2/gtfs/realtime/metro',
    'regional': 'v1/gtfs/realtime/nswtrains',
}
ALERT_FEEDS: Final[dict[str, str]] = {
    'bus': 'v2/gtfs/alerts/buses',
    'train': 'v2/gtfs/alerts/sydneytrains',
    'metro': 'v2/gtfs/alerts/metro',
    'light_rail': 'v2/gtfs/alerts/lightrail',
    'regional': 'v2/gtfs/alerts/nswtrains',
    'ferry': 'v2/gtfs/alerts/ferries',
}
POSITION_FEEDS: Final[dict[str, tuple[str, ...]]] = {
    'bus': ('v1/gtfs/vehiclepos/buses',),
    'train': ('v2/gtfs/vehiclepos/sydneytrains',),
    'metro': ('v2/gtfs/vehiclepos/metro',),
    'regional': ('v1/gtfs/vehiclepos/nswtrains',),
    'ferry': ('v1/gtfs/vehiclepos/ferries/sydneyferries',),
    'light_rail': (
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v1/gtfs/vehiclepos/lightrail/newcastle',
        'v1/gtfs/vehiclepos/lightrail/parramatta',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
    ),
}
LABELS: Final[dict[str, str]] = {
    'v1/gtfs/realtime/buses': 'Buses trip updates',
    'v2/gtfs/realtime/sydneytrains': 'Sydney Trains trip updates',
    'v2/gtfs/realtime/metro': 'Metro trip updates',
    'v1/gtfs/realtime/nswtrains': 'NSW TrainLink trip updates',
    'v2/gtfs/alerts/buses': 'Buses alerts',
    'v2/gtfs/alerts/sydneytrains': 'Sydney Trains alerts',
    'v2/gtfs/alerts/metro': 'Metro alerts',
    'v2/gtfs/alerts/lightrail': 'Light rail alerts',
    'v2/gtfs/alerts/nswtrains': 'NSW TrainLink alerts',
    'v2/gtfs/alerts/ferries': 'Ferries alerts',
    'v1/gtfs/vehiclepos/buses': 'Buses positions',
    'v2/gtfs/vehiclepos/sydneytrains': 'Sydney Trains positions',
    'v2/gtfs/vehiclepos/metro': 'Metro positions',
    'v1/gtfs/vehiclepos/nswtrains': 'NSW TrainLink positions',
    'v1/gtfs/vehiclepos/ferries/sydneyferries': 'Ferries positions',
    'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast': (
        'Light rail positions (cbdandsoutheast)'
    ),
    'v2/gtfs/vehiclepos/lightrail/innerwest': (
        'Light rail positions (innerwest)'
    ),
    'v1/gtfs/vehiclepos/lightrail/parramatta': (
        'Light rail positions (parramatta)'
    ),
    'v1/gtfs/vehiclepos/lightrail/newcastle': (
        'Light rail positions (newcastle)'
    ),
    'v9/other': 'v9/other',
}
KIND_SOURCES: Final[dict[str, frozenset[str]]] = {
    'bus': frozenset({'bus'}),
    'school': frozenset({'bus'}),
    'replacement': frozenset({'bus'}),
    'train': frozenset({'train'}),
    'metro': frozenset({'metro'}),
    'light_rail': frozenset({'light_rail'}),
    'ferry': frozenset({'ferry'}),
    'coach': frozenset({'coach'}),
}


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
    x, y = to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION)
    return (x - half, y - half, x + half, y + half)


def test_plan_answers_as_core_feeds_did() -> None:
    """Every source's feeds, every label and limit are today's."""
    for source in SydneyFeed:
        assert PLAN.trip_feed(source=source) == TRIP_FEEDS.get(source)
        assert PLAN.alert_feeds(sources=[source]) == (
            (ALERT_FEEDS[source],) if source in ALERT_FEEDS else ()
        )
        assert PLAN.position_feeds(sources=[source], box=None) == (
            POSITION_FEEDS.get(source, ())
        )
    for feed, label in LABELS.items():
        assert PLAN.label(feed=feed) == label
    for mode, sources in KIND_SOURCES.items():
        assert PLAN.sources_of(mode=mode) == sources
    assert PLAN.sources_of(mode='tram') == frozenset()
    assert PLAN.position_sources() == frozenset(POSITION_FEEDS)
    assert PLAN.kind(feed='v1/gtfs/vehiclepos/buses') is FeedKind.POSITIONS
    assert PLAN.kind(feed='v2/gtfs/alerts/metro') is FeedKind.ALERTS
    assert PLAN.kind(feed='v1/gtfs/realtime/buses') is FeedKind.TRIPS
    for feed in (*TRIP_FEEDS.values(), *ALERT_FEEDS.values()):
        expected = 401 if feed == 'v2/gtfs/alerts/metro' else None
        assert PLAN.single_line_type(feed=feed) == expected
    assert PLAN.daily_limit == 60_000


def test_sydney_city_carries_the_plan() -> None:
    """Sydney's city reads its feeds from a plan in its projection."""
    assert isinstance(SYDNEY.feeds, SydneyFeeds)
    assert SYDNEY.feeds.projection is SYDNEY_PROJECTION


def test_position_feeds_are_sorted_and_unique() -> None:
    """Several sources give each feed once, sorted; COACH has none."""
    assert PLAN.position_feeds(
        sources=[SydneyFeed.TRAIN, SydneyFeed.BUS, SydneyFeed.BUS],
        box=None,
    ) == (
        'v1/gtfs/vehiclepos/buses',
        'v2/gtfs/vehiclepos/sydneytrains',
    )
    assert PLAN.position_feeds(sources=[SydneyFeed.COACH], box=None) == ()


def test_alert_feeds_are_sorted_and_unique() -> None:
    """Several sources give each alert feed once, sorted."""
    assert PLAN.alert_feeds(
        sources=[SydneyFeed.TRAIN, SydneyFeed.BUS, SydneyFeed.BUS],
    ) == (
        'v2/gtfs/alerts/buses',
        'v2/gtfs/alerts/sydneytrains',
    )


def test_box_keeps_the_light_rail_line_in_the_area() -> None:
    """Around Parramatta only the Parramatta light rail feed is kept."""
    assert PLAN.position_feeds(
        sources=[SydneyFeed.LIGHT_RAIL, SydneyFeed.BUS],
        box=box_around(lon=151.003, lat=-33.817),
    ) == (
        'v1/gtfs/vehiclepos/buses',
        'v1/gtfs/vehiclepos/lightrail/parramatta',
    )


def test_box_keeps_both_lines_that_meet() -> None:
    """At Central the CBD and Inner West light rail lines both stay."""
    assert PLAN.position_feeds(
        sources=[SydneyFeed.LIGHT_RAIL],
        box=box_around(lon=151.206, lat=-33.884),
    ) == (
        'v1/gtfs/vehiclepos/lightrail/cbdandsoutheast',
        'v2/gtfs/vehiclepos/lightrail/innerwest',
    )


def test_box_drops_newcastle_from_a_city_wide_map() -> None:
    """A map of all of Sydney still has no Newcastle light rail."""
    feeds = PLAN.position_feeds(
        sources=[SydneyFeed.LIGHT_RAIL],
        box=box_around(lon=151.1, lat=-33.85, half=15_000),
    )
    assert 'v1/gtfs/vehiclepos/lightrail/newcastle' not in feeds
    assert len(feeds) == 3


def test_parsers_place_positions_in_the_projection() -> None:
    """The positions reader places vehicles in the plan's projection."""
    data = (REAL / 'v1_gtfs_vehiclepos_buses.pb').read_bytes()
    read = PLAN.parsers(feed='v1/gtfs/vehiclepos/buses').positions(
        data=data, fetched_at=AT,
    )
    direct = parse_positions(
        data=data, fetched_at=AT, projection=SYDNEY_PROJECTION,
    )
    assert read == direct
    assert read.vehicles
