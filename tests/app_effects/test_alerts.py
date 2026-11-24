"""Tests for app effects: alerts."""

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, field, replace

import pytest
from google.transit import gtfs_realtime_pb2

from paperroute_sydney.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_sydney.sydney_feeds import SydneyFeeds
from paperroute_testkit.fakes import EmptyPlacedTimetable
from paperroute_transit.app_effects import parse_feed
from paperroute_transit.core.alert_model import AlertPlaces, AlertsFetched
from paperroute_transit.core.feed_plan import FeedKind, FeedParsers
from paperroute_transit.core.messages import FetchFeed
from tests.app_effects.helpers import RecordingParsers
from tests.app_factories import AT, REAL, ports_of, posted_by

ALERTS_FEED = 'v2/gtfs/alerts/sydneytrains'


METRO_FEED = 'v2/gtfs/alerts/metro'


WESTERN = '5ef3cb29-30b3-5e60-bd7a-343519598275'


@dataclass
class PlacesTimetable(EmptyPlacedTimetable):
    """A timetable that answers alert lookups from fixed places."""

    places: AlertPlaces = AlertPlaces(routes={}, stops={})
    asked: int = 0
    route_types: list[int | None] = field(default_factory=list)

    def alert_places(
        self,
        *,
        route_ids: Collection[str],
        stop_ids: Collection[str],
        route_type: int | None = None,
    ) -> AlertPlaces:
        """
        Give the fixed places and count the question.

        Parameters
        ----------
        route_ids : Collection[str]
            Ignored.
        stop_ids : Collection[str]
            Ignored.
        route_type : int | None
            Noted; the fixed only line is given for type 401 alone.

        Returns
        -------
        AlertPlaces
            The fixed places, with no only line unless `route_type` is
            401.
        """
        self.asked += 1
        self.route_types.append(route_type)
        if route_type == 401:
            return self.places
        return replace(self.places, only_line=None)


def alert_event(
    *, timetable: PlacesTimetable, feed: str, data: bytes
) -> AlertsFetched:
    """
    Parse an alerts feed through `parse_feed`.

    Parameters
    ----------
    timetable : PlacesTimetable
        Answers the lookups.
    feed : str
        Feed path.
    data : bytes
        Feed body.

    Returns
    -------
    AlertsFetched
        The event.
    """
    event, count = parse_feed(
        timetable=timetable,
        plan=SYDNEY.feeds,
        effect=FetchFeed(feed=feed, kind=FeedKind.ALERTS),
        data=data,
        fetched_at=AT,
    )
    assert isinstance(event, AlertsFetched)
    assert count == len(event.snapshot.alerts)
    return event


class RecordingPlan(SydneyFeeds):
    """Sydney's feeds, read by recording readers."""

    def __init__(self, *, recorder: RecordingParsers) -> None:
        super().__init__(projection=SYDNEY_PROJECTION)
        self.recorder = recorder

    def parsers(self, *, feed: str) -> FeedParsers:
        """
        Give the recording readers for any feed.

        Parameters
        ----------
        feed : str
            Feed path, ignored.

        Returns
        -------
        FeedParsers
            The recorder's readers.
        """
        del feed
        return FeedParsers(
            trips=self.recorder.trips,
            positions=self.recorder.positions,
            alerts=self.recorder.alerts,
        )


@pytest.mark.parametrize(('feed', 'kind'), [
    (ALERTS_FEED, FeedKind.ALERTS),
    ('v1/gtfs/vehiclepos/buses', FeedKind.POSITIONS),
    ('v1/gtfs/realtime/buses', FeedKind.TRIPS),
])
def test_parse_uses_feed_parsers(feed: str, kind: FeedKind) -> None:
    """Each feed's body goes to the plan's reader for what it carries."""
    recorder = RecordingParsers()
    event, count = parse_feed(
        timetable=PlacesTimetable(),
        plan=RecordingPlan(recorder=recorder),
        effect=FetchFeed(feed=feed, kind=kind),
        data=b'body',
        fetched_at=AT,
    )
    assert recorder.read == [(kind.value, b'body')]
    assert count == 0
    assert event.feed == feed


def test_alert_feed_parsed_to_event() -> None:
    """An alerts fetch gives AlertsFetched with the routes resolved."""
    timetable = PlacesTimetable(
        places=AlertPlaces(
            routes={'WST_2c': 'T1'},
            stops={},
            lines={'WST_2c': 'sydneytrains:WST_2c'},
        )
    )
    data = (REAL / 'v2_gtfs_alerts_sydneytrains.pb').read_bytes()
    event = alert_event(timetable=timetable, feed=ALERTS_FEED, data=data)
    assert event.feed == ALERTS_FEED
    assert len(event.snapshot.alerts) == 5
    western = next(
        found
        for found in event.snapshot.alerts
        if found.alert_id == WESTERN
    )
    routes = {item.route_id: item.route for item in western.informed}
    assert routes['WST_2c'] == 'T1'
    assert routes['WST_1a'] is None
    lines = {item.route_id: item.line for item in western.informed}
    assert lines['WST_2c'] == 'sydneytrains:WST_2c'
    assert lines['WST_1a'] is None
    assert timetable.asked == 1
    assert timetable.route_types == [None]


def metro_alert(
    *, routes: tuple[str, ...] = ('4T.C.2301', 'SMNW_M1'),
) -> bytes:
    """
    Build a metro alerts body naming some route IDs.

    Parameters
    ----------
    routes : tuple[str, ...]
        Route IDs of its informed entities.

    Returns
    -------
    bytes
        Feed body.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    entity = feed.entity.add()
    entity.id = 'metro-1'
    for route in routes:
        entity.alert.informed_entity.add().route_id = route
    return bytes(feed.SerializeToString())


def test_metro_feed_routes_resolve_to_the_line() -> None:
    """Every route of the metro feed is the single metro line."""
    timetable = PlacesTimetable(
        places=AlertPlaces(routes={}, stops={}, only_line='M1')
    )
    metro = alert_event(
        timetable=timetable, feed=METRO_FEED, data=metro_alert()
    )
    assert [item.route for item in metro.snapshot.alerts[0].informed] == [
        'M1',
        'M1',
    ]
    trains = alert_event(
        timetable=timetable, feed=ALERTS_FEED, data=metro_alert()
    )
    assert [item.route for item in trains.snapshot.alerts[0].informed] == [
        None,
        None,
    ]
    assert timetable.route_types == [401, None]


def test_metro_feed_keeps_resolved_routes() -> None:
    """A metro-feed route the timetable resolves keeps its own name."""
    timetable = PlacesTimetable(
        places=AlertPlaces(routes={'WST_2c': 'T1'}, stops={}, only_line='M1')
    )
    metro = alert_event(
        timetable=timetable,
        feed=METRO_FEED,
        data=metro_alert(routes=('WST_2c', '4T.C.2301')),
    )
    assert [item.route for item in metro.snapshot.alerts[0].informed] == [
        'T1',
        'M1',
    ]


def test_alert_stops_filled_from_families() -> None:
    """An informed stop carries its family; an unknown one carries none."""
    family = frozenset({'200060', '2000321'})
    timetable = PlacesTimetable(
        places=AlertPlaces(routes={}, stops={'200060': family})
    )
    data = (REAL / 'v2_gtfs_alerts_sydneytrains.pb').read_bytes()
    event = alert_event(timetable=timetable, feed=ALERTS_FEED, data=data)
    stops = {
        item.stop_id: item.stops
        for found in event.snapshot.alerts
        for item in found.informed
        if item.stop_id
    }
    assert stops['200060'] == family
    assert stops['213510'] == frozenset()


@dataclass
class AlertFailingTimetable(EmptyPlacedTimetable):
    """A timetable whose alert lookup fails."""

    def alert_places(
        self,
        *,
        route_ids: Collection[str],
        stop_ids: Collection[str],
        route_type: int | None = None,
    ) -> AlertPlaces:
        """
        Fail.

        Parameters
        ----------
        route_ids : Collection[str]
            Ignored.
        stop_ids : Collection[str]
            Ignored.
        route_type : int | None
            Ignored.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.OperationalError('disk I/O error')


async def test_alert_fetch_failure_posts_feed_failed() -> None:
    """A failing timetable lookup posts FeedFailed for the feed."""
    ports = ports_of(timetable=AlertFailingTimetable())
    posted = await posted_by(
        ports=ports,
        effects=[FetchFeed(feed=ALERTS_FEED, kind=FeedKind.ALERTS)],
    )
    assert [type(event).__name__ for event in posted] == ['FeedFailed']
