"""Tests for timetable db: stops and services."""

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from paperroute_sydney.city import SydneyFeed
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.factories import pin_of
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build.gtfs_files import Source
from tests.adapters.timetable_db.helpers import (
    HOLLIS_TO_PARRA,
    at,
    bundle_db,
    opened,
    regional_db,
)
from tests.build.feeds import (
    DAILY,
    STATIONS,
    build_db,
    regional_sources,
    stop_times,
)


def test_origin_reads_the_recorded_origin(tmp_path: Path) -> None:
    """The origin is read from the file's meta."""
    db = bundle_db(tmp_path=tmp_path, days=1)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO meta VALUES ('origin', '0.0,51.5')")
        conn.commit()
    with opened(path=db) as repo:
        assert repo.origin() == '0.0,51.5'


def test_stop_name_and_unknown_stop(repo: SqliteTimetableRepository) -> None:
    """Names come from stops; unknown IDs raise KeyError."""
    assert repo.stop(stop_id='215020').name == 'Parramatta Station'
    with pytest.raises(KeyError):
        repo.stop(stop_id='nope')


def test_departures_kept_xpt_stays_regional(tmp_path: Path) -> None:
    """A feed XPT that replaced a bundle copy is Regional with its alias."""
    db = regional_db(tmp_path=tmp_path, days=1)
    with opened(path=db) as repo:
        found = repo.departures(
            pin=pin_of('200060', '215020'),
            start=at(hour=7, minute=0),
            end=at(hour=8, minute=0),
        )
    assert [(d.trip_id, d.source, d.aliases) for d in found] == [
        ('NT31-feed', SydneyFeed.REGIONAL, ('NT31-bundle',))
    ]


def test_serves_agencies_reads_given_agencies(tmp_path: Path) -> None:
    """A stop answers for the agencies asked about, not a fixed one."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 16),
        days=1,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with opened(path=db) as repo:
        assert repo.serves_agencies(stop_ids=['2000321'], agencies={'711'})
        assert not repo.serves_agencies(
            stop_ids=['2000321'], agencies={'999'},
        )


def test_departures_partial_xpt_listed_once_per_day(
    tmp_path: Path,
) -> None:
    """A partly covered bundle XPT shows only on the dates it's uncovered."""
    db = regional_db(tmp_path=tmp_path, days=2, trim=True)
    pin = pin_of('200060', '215020')
    with opened(path=db) as repo:
        found = [
            repo.departures(
                pin=pin,
                start=at(hour=7, minute=0, day=day),
                end=at(hour=8, minute=0, day=day),
            )
            for day in (16, 17)
        ]
    assert [[(d.trip_id, d.source) for d in day] for day in found] == [
        [('NT31-feed', SydneyFeed.REGIONAL)],
        [('NT31-bundle', SydneyFeed.REGIONAL)],
    ]


def test_departures_before_daylight_saving_jump(tmp_path: Path) -> None:
    """A 00:30 trip on the change day leaves at 23:30 the night before."""
    archive = write_feed(path=tmp_path / 'dst.zip', files={
        'stops.txt': STATIONS,
        'routes.txt': [{
            'route_id': 'r1', 'agency_id': '2436', 'route_short_name': '1',
            'route_long_name': 'Night', 'route_type': '700',
            'route_color': '', 'route_text_color': ''}],
        'calendar.txt': [{'service_id': 'daily', **DAILY}],
        'trips.txt': [{
            'route_id': 'r1', 'service_id': 'daily', 'trip_id': 'night',
            'trip_headsign': 'Central', 'direction_id': '0',
            'block_id': ''}],
        'stop_times.txt': stop_times(trip='night', times=[
            ('2150411', '00:30:00'), ('2000321', '01:00:00')]),
    })
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 3),
        days=3,
        sources=[Source(name='bundle', archive=archive)],
    )
    with opened(path=db) as repo:
        found = repo.departures(
            pin=pin_of('215020', '200060'),
            start=at(hour=23, minute=0, day=3),
            end=at(hour=1, minute=0, day=4),
        )
    assert [(d.trip_id, d.service_day) for d in found] == [
        ('night', date(2026, 10, 4))
    ]
    assert found[0].departs.astimezone(UTC) == datetime(
        2026, 10, 3, 13, 30, tzinfo=UTC
    )


def test_departures_accepts_utc_times(
    repo: SqliteTimetableRepository,
) -> None:
    """Service days come from Sydney dates even when given UTC times."""
    found = repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=8, minute=0).astimezone(UTC),
        end=at(hour=9, minute=0).astimezone(UTC),
    )
    assert [d.trip_id for d in found] == ['bus-0803']
