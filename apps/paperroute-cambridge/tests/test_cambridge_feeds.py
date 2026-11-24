"""Tests for Cambridge's feed plan."""

from datetime import date, datetime

from paperroute_cambridge.area import CAMBRIDGE_PROJECTION
from paperroute_cambridge.cambridge_feeds import (
    DAILY_LIMIT,
    ESTIMATES_FEED,
    POSITIONS_FEED,
    SOURCES,
    CambridgeFeeds,
)
from paperroute_cambridge.city import PEAK_WINDOWS
from paperroute_transit.core.feed_plan import FeedKind, FeedPlan
from tests.adapters.feed_bodies import NOW, positions_body

DAY = date(2026, 11, 17)
PLAN: FeedPlan = CambridgeFeeds(projection=CAMBRIDGE_PROJECTION)


def test_daily_limit_is_the_constant() -> None:
    """The daily limit is the constant."""
    assert PLAN.daily_limit == DAILY_LIMIT == 4_000


def test_only_buses_have_a_trips_feed() -> None:
    """Buses have the estimates feed; coaches have none; nothing has alerts."""
    assert PLAN.trip_feed(source='bus') == ESTIMATES_FEED
    assert PLAN.trip_feed(source='coach') is None
    assert not PLAN.alert_feeds(sources=SOURCES)
    assert PLAN.single_line_type(feed=POSITIONS_FEED) is None


def test_estimates_polling_fits_under_the_first_guard() -> None:
    """30 s in the peaks and 2 min otherwise stays near 1,200 a day."""
    peak = sum(
        (
            datetime.combine(DAY, window.end)
            - datetime.combine(DAY, window.start)
        ).total_seconds()
        for window in PEAK_WINDOWS
    )
    calls = peak / 30 + (86_400 - peak) / 120
    assert calls == 1_170
    assert calls < 0.8 * DAILY_LIMIT


def test_position_sources_are_bus_and_coach() -> None:
    """Position sources are bus and coach."""
    assert PLAN.position_sources() == frozenset({'bus', 'coach'})


def test_position_feeds_is_one_feed_for_a_bus_or_coach_source() -> None:
    """Position feeds is one feed for a bus or coach source."""
    assert PLAN.position_feeds(
        sources=('coach',),
        box=None,
    ) == (POSITIONS_FEED,)
    assert PLAN.position_feeds(sources=('bus', 'coach'), box=None) == (
        POSITIONS_FEED,
    )
    assert not PLAN.position_feeds(sources=('train',), box=None)


def test_sources_of_maps_a_mode_to_its_source() -> None:
    """Sources of maps a mode to its source."""
    assert PLAN.sources_of(mode='bus') == frozenset({'bus'})
    assert PLAN.sources_of(mode='coach') == frozenset({'coach'})
    assert PLAN.sources_of(mode='train') == frozenset()


def test_kind_and_label() -> None:
    """The kind is positions and the label names the feed."""
    assert PLAN.kind(feed=POSITIONS_FEED) is FeedKind.POSITIONS
    assert PLAN.label(feed=POSITIONS_FEED) == 'Bus and coach positions'
    assert PLAN.label(feed='other') == 'other'


def test_parsers_read_positions_in_the_projection() -> None:
    """The parsers read a positions body and are shared by every feed."""
    readers = PLAN.parsers(feed=POSITIONS_FEED)
    assert readers is PLAN.parsers(feed='other')
    body = positions_body(vehicles=(('3300', 'VJ1', '57428', 'L7', 990),))
    snapshot = readers.positions(data=body, fetched_at=NOW)
    assert [vehicle.label for vehicle in snapshot.vehicles] == ['L7']


def test_estimates_are_worked_out_from_the_positions_feed() -> None:
    """
    The estimates feed comes from the positions feed and is never fetched.
    """
    assert PLAN.estimated_from(feed=ESTIMATES_FEED) == POSITIONS_FEED
    assert PLAN.estimated_from(feed=POSITIONS_FEED) is None
    assert PLAN.kind(feed=ESTIMATES_FEED) is FeedKind.ESTIMATES
    assert PLAN.label(feed=ESTIMATES_FEED) == 'Bus times from positions'
