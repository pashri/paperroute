"""Tests for the made-up city the transit kit's tests run against."""

import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from paperroute_device.assets import assets
from paperroute_testkit.city_setting import (
    UNDER_TEST,
    city_under_test,
    line_mode,
    source_of,
    street_mode,
)
from paperroute_testkit.core.factories import START, TRAIN
from paperroute_testkit.fake_assets import TEST_ASSETS
from paperroute_testkit.fake_city import TEST_CITY
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.modes import mode_problems


def test_city_under_test_is_the_test_city() -> None:
    """The package conftest sets the test city and the kit's web files."""
    assert city_under_test() is TEST_CITY
    assert assets() == TEST_ASSETS
    assert (TEST_ASSETS.web / 'palette.css').is_file()


def test_test_city_modes_are_valid() -> None:
    """The test city's modes declare nothing inconsistent."""
    assert not mode_problems(modes=TEST_CITY.modes)


def test_testkit_values_come_from_the_test_city() -> None:
    """The test kit's times and modes are the test city's."""
    assert START.tzinfo is TEST_CITY.zone
    assert TRAIN.kind == line_mode() == 'rail'
    assert street_mode() == 'bus'


def test_city_modes_missing_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A city with no line, tagged or fed mode cannot give one."""
    monkeypatch.setattr(UNDER_TEST, 'city', replace(TEST_CITY, modes=()))
    with pytest.raises(ValueError, match='chosen by line'):
        line_mode()
    with pytest.raises(ValueError, match='place tags'):
        street_mode()
    with pytest.raises(ValueError, match='no feed source'):
        source_of(mode='ferry')


@pytest.mark.parametrize(('route_type', 'short_name', 'kind'), [
    (3, '10', 'bus'),
    (0, 'T1', 'tram'),
    (2, 'R1', 'rail'),
    (2, '', None),
    (4, 'F1', None),
])
def test_test_city_classifies_routes(
    route_type: int, short_name: str, kind: str | None,
) -> None:
    """Each route type maps to one mode, which is also its source."""
    found = TEST_CITY.classify(
        route_type=route_type, agency_id=None, short_name=short_name,
    )
    assert found.kind == kind
    assert found.source == (kind or 'other')


def test_test_city_rules() -> None:
    """Rail lines list first, and merging changes nothing."""
    catalogue = TEST_CITY.catalogue
    assert catalogue.line_group(number='R2') == 0
    assert catalogue.line_group(number='42') == 1
    with closing(sqlite3.connect(':memory:')) as conn:
        report = TEST_CITY.timetable.merge(conn=conn)
    assert (report.dropped, report.partial, report.overnight) == (0, 0, 0)


def test_test_city_feeds() -> None:
    """Each mode has one feed of each kind."""
    feeds = TEST_CITY.feeds
    assert feeds.trip_feed(source='rail') == 'trips/rail'
    assert feeds.trip_feed(source='other') is None
    assert feeds.position_feeds(sources=['tram', 'bus', 'x'], box=None) == (
        'positions/bus',
        'positions/tram',
    )
    assert feeds.alert_feeds(sources=['rail', 'x']) == ('alerts/rail',)
    assert feeds.sources_of(mode='tram') == frozenset({'tram'})
    assert feeds.sources_of(mode='ferry') == frozenset()
    assert source_of(mode='bus') == 'bus'
    assert feeds.kind(feed='alerts/rail') is FeedKind.ALERTS
    assert feeds.kind(feed='other') is FeedKind.TRIPS
    assert feeds.label(feed='trips/bus') == 'trips/bus'
    assert feeds.single_line_type(feed='alerts/rail') is None
    assert feeds.position_sources() == frozenset({'bus', 'rail', 'tram'})
    assert feeds.daily_limit > 0
    assert feeds.parsers(feed='trips/bus').trips is not None
