"""Tests for GTFS-realtime alert decoding."""

import logging
from datetime import datetime
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2

from paperroute.adapters.gtfs_alerts import english, parse_alerts
from paperroute.adapters.tfnsw import FeedError
from paperroute_transit.core.alert_model import Alert, AlertsSnapshot, Period
from paperroute_transit.core.model import SYDNEY

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=SYDNEY)
FIXTURE = (
    Path(__file__).parent.parent
    / 'fixtures'
    / 'real'
    / 'v2_gtfs_alerts_sydneytrains.pb'
)
FEED = 'v2/gtfs/alerts/sydneytrains'
FERRY_FIXTURE = FIXTURE.parent / 'v2_gtfs_alerts_ferries.pb'
FERRY_FEED = 'v2/gtfs/alerts/ferries'
WESTERN = '5ef3cb29-30b3-5e60-bd7a-343519598275'
TWO_WINDOWS = '550a1f97-0e0c-5ea5-bc74-2f2e17e494f7'
CENTRAL = '7e669567-64e5-5fec-83a2-7332090518c3'
STRATHFIELD = '2711aca3-8f6e-5edd-8af3-bf47829b957d'


def snapshot(*, data: bytes | None = None) -> AlertsSnapshot:
    """
    Parse the fixture, or the given bytes.

    Parameters
    ----------
    data : bytes | None
        Feed body, or None for the fixture.

    Returns
    -------
    AlertsSnapshot
        The parsed alerts.
    """
    body = FIXTURE.read_bytes() if data is None else data
    return parse_alerts(data=body, feed=FEED, fetched_at=NOW)


def alert(*, alert_id: str) -> Alert:
    """
    Find one fixture alert.

    Parameters
    ----------
    alert_id : str
        Entity ID.

    Returns
    -------
    Alert
        The alert.
    """
    return next(
        found for found in snapshot().alerts if found.alert_id == alert_id
    )


def sydney(
    *, month: int, day: int, hour: int, minute: int, second: int = 0
) -> datetime:
    """
    Build a Sydney time in 2026.

    Parameters
    ----------
    month : int
        Month.
    day : int
        Day.
    hour : int
        Hour.
    minute : int
        Minute.
    second : int
        Second.

    Returns
    -------
    datetime
        An aware datetime.
    """
    return datetime(
        2026, month, day, hour, minute, second, tzinfo=SYDNEY
    )


def test_parses_fixture() -> None:
    """Every alert is read, in feed order, with the fetch time kept."""
    result = snapshot()
    assert result.feed == FEED
    assert result.fetched_at == NOW
    assert [found.alert_id[:8] for found in result.alerts] == [
        '25385491',
        '5ef3cb29',
        '550a1f97',
        '7e669567',
        '2711aca3',
    ]


def test_western_line_alert() -> None:
    """Text, window and routes of a route alert are read."""
    found = alert(alert_id=WESTERN)
    assert found.header == (
        'T1 Western Line: Trains run to a changed timetable and stopping '
        'pattern. Buses replace some trains'
    )
    assert found.description.startswith(
        'Saturday 24 and Sunday 25 October\n'
        'Trains run to a changed timetable and stopping pattern between:'
    )
    assert '<' not in found.description
    assert found.periods == (
        Period(
            start=sydney(month=10, day=24, hour=2, minute=0),
            end=sydney(month=10, day=26, hour=2, minute=0, second=59),
        ),
    )
    assert len(found.informed) == 4
    assert {item.route_id for item in found.informed} == {
        'WST_1a',
        'WST_1b',
        'WST_2c',
        'WST_2d',
    }
    assert all(
        item.stop_id is None and item.trip_id is None
        for item in found.informed
    )


def test_two_windows() -> None:
    """An alert with two active periods keeps both."""
    assert alert(alert_id=TWO_WINDOWS).periods == (
        Period(
            start=sydney(month=10, day=26, hour=21, minute=50),
            end=sydney(month=10, day=27, hour=1, minute=30),
        ),
        Period(
            start=sydney(month=10, day=27, hour=21, minute=50),
            end=sydney(month=10, day=28, hour=1, minute=30),
        ),
    )


def test_open_end_period() -> None:
    """An end of zero is read as no end."""
    found = alert(alert_id=CENTRAL)
    assert found.header == 'Station Update - Central'
    assert found.periods == (
        Period(
            start=sydney(month=8, day=6, hour=10, minute=30, second=6),
            end=None,
        ),
    )
    assert {item.stop_id for item in found.informed} == {'200060'}


def test_station_update_stop() -> None:
    """A station alert names its stop on every entity, and many routes."""
    found = alert(alert_id=STRATHFIELD)
    assert found.header == 'Station Update - Strathfield'
    assert len(found.informed) == 98
    assert {item.stop_id for item in found.informed} == {'213510'}
    routes = {item.route_id for item in found.informed}
    assert 'WST_2c' in routes
    assert any(route.startswith('4T.C.') for route in routes if route)
    assert any(route.startswith('4T.T.') for route in routes if route)


def test_bad_entity_skipped(caplog: pytest.LogCaptureFixture) -> None:
    """An entity whose alert cannot be read is skipped with one warning."""
    entity = b'\x0a\x03bad\x2a\x04\xff\xff\xff\xff'
    data = FIXTURE.read_bytes() + b'\x12' + bytes([len(entity)]) + entity
    with caplog.at_level(logging.WARNING):
        result = snapshot(data=data)
    assert len(result.alerts) == 5
    skipped = [
        record for record in caplog.records if 'undecodable' in record.message
    ]
    assert len(skipped) == 1


def test_out_of_range_time_skipped(caplog: pytest.LogCaptureFixture) -> None:
    """An alert with an impossible time is skipped with one warning."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    for name in ('bad-1', 'bad-2'):
        entity = feed.entity.add()
        entity.id = name
        entity.alert.active_period.add().start = 2**60
    good = feed.entity.add()
    good.id = 'good'
    good.alert.active_period.add().start = 1792767600
    with caplog.at_level(logging.WARNING):
        result = snapshot(data=feed.SerializeToString())
    assert [found.alert_id for found in result.alerts] == ['good']
    assert len(caplog.records) == 1


def test_not_a_feed_raises() -> None:
    """A body that is not a feed raises FeedError."""
    with pytest.raises(FeedError):
        snapshot(data=b'not protobuf')


def test_english_prefers_en_then_plain() -> None:
    """The en translation wins, then any that is not html, else empty."""
    text = gtfs_realtime_pb2.TranslatedString()
    assert english(text=text) == ''
    for language, words in (('en/html', '<b>x</b>'), ('fr', 'bonjour')):
        item = text.translation.add()
        item.language = language
        item.text = words
    assert english(text=text) == 'bonjour'
    item = text.translation.add()
    item.language = 'en'
    item.text = 'hello'
    assert english(text=text) == 'hello'
    only_html = gtfs_realtime_pb2.TranslatedString()
    html = only_html.translation.add()
    html.language = 'en/html'
    html.text = '<b>x</b>'
    assert english(text=only_html) == ''


def test_ferry_fixture_parsed() -> None:
    """The ferry fixture's four alerts, routes, stops and periods are read."""
    body = FERRY_FIXTURE.read_bytes()
    result = parse_alerts(data=body, feed=FERRY_FEED, fetched_at=NOW)
    assert len(result.alerts) == 4
    found = {item.alert_id[:8]: item for item in result.alerts}
    closure = found['30ef7969']
    assert closure.header == 'Rose Bay Wharf temporary closure'
    assert any(
        item.route_id == '9-F9-sj2-1' and item.stop_id == '202972'
        for item in closure.informed
    )
    periods = found['84aad6bc'].periods
    assert len(periods) == 1
    assert periods[0].end is None
