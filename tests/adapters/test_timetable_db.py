"""Tests for the SQLite timetable repository."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.dedupe import merge_rail
from paperroute.build.timetable import Source
from paperroute.core.model import SYDNEY, Mode, Pin
from tests.build.feeds import (
    DAILY,
    STATIONS,
    build_db,
    bundle_rail_feed,
    bus_feed,
    regional_sources,
    stop_times,
    sydneytrains_feed,
)
from tests.helpers import write_feed

HOLLIS_TO_PARRA = Pin(origin='2145615', destination='215020')
PARRA_TO_CENTRAL = Pin(origin='215020', destination='200060')


@pytest.fixture(name='repo')
def _repo(tmp_path: Path) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the small merged fixture timetable.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the test.
    """
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(
                name='sydneytrains',
                archive=sydneytrains_feed(path=tmp_path / 's.zip'),
            ),
            Source(
                name='bundle',
                archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
            ),
            Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
    with closing(SqliteTimetableRepository(path=db)) as repo:
        yield repo


def at(*, hour: int, minute: int, day: int = 16) -> datetime:
    """
    A Sydney time in October 2026.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.
    day : int
        Day of month.

    Returns
    -------
    datetime
        Aware datetime.
    """
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY)


def test_departures_bus_with_stand_and_times(
    repo: SqliteTimetableRepository,
) -> None:
    """A bus departure carries route, mode, stand and both times."""
    found = repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=0),
    )
    assert [(d.trip_id, d.route, d.mode, d.platform) for d in found] == [
        ('bus-0803', '606', Mode.BUS, '')
    ]
    assert found[0].departs == at(hour=8, minute=3)
    assert found[0].arrives == at(hour=8, minute=39)


def test_departures_uses_station_children_and_skips_no_pickup(
    repo: SqliteTimetableRepository,
) -> None:
    """Platforms under a station count; a stop with no pickup doesn't."""
    found = repo.departures(
        pin=PARRA_TO_CENTRAL,
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=30),
    )
    assert [d.trip_id for d in found] == ['S-0835', 'B-0900']
    assert found[0].aliases == ('B-0835',)
    assert found[0].platform == '1'


def test_departures_excludes_wrong_direction(
    repo: SqliteTimetableRepository,
) -> None:
    """A trip reaching the destination before the origin isn't direct."""
    found = repo.departures(
        pin=PARRA_TO_CENTRAL,
        start=at(hour=8, minute=0),
        end=at(hour=8, minute=40),
    )
    assert 'B-NB' not in [d.trip_id for d in found]


def test_departures_includes_previous_service_day_after_midnight(
    repo: SqliteTimetableRepository,
) -> None:
    """A 25:10 trip from the 16th's service day leaves at 1:10 on the 17th."""
    found = repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=1, minute=0, day=17),
        end=at(hour=1, minute=30, day=17),
    )
    assert [(d.trip_id, d.departs) for d in found] == [
        ('bus-2510', at(hour=1, minute=10, day=17))
    ]


def test_stop_name_and_unknown_stop(repo: SqliteTimetableRepository) -> None:
    """Names come from stops; unknown IDs raise KeyError."""
    assert repo.stop_name(stop_id='215020') == 'Parramatta Station'
    with pytest.raises(KeyError):
        repo.stop_name(stop_id='nope')


def test_departures_kept_xpt_stays_regional(tmp_path: Path) -> None:
    """A feed XPT that replaced a bundle copy is Regional with its alias."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 16),
        days=1,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.departures(
            pin=Pin(origin='200060', destination='215020'),
            start=at(hour=7, minute=0),
            end=at(hour=8, minute=0),
        )
    assert [(d.trip_id, d.mode, d.aliases) for d in found] == [
        ('NT31-feed', Mode.REGIONAL, ('NT31-bundle',))
    ]


def test_departures_partial_xpt_listed_once_per_day(
    tmp_path: Path,
) -> None:
    """A partly covered bundle XPT shows only on the dates it's uncovered."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 16),
        days=2,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "DELETE FROM services WHERE service_id = 'sydneytrains:s-daily' "
            "AND date = '2026-10-17'"
        )
        conn.commit()
        merge_rail(conn=conn)
    pin = Pin(origin='200060', destination='215020')
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = [
            repo.departures(
                pin=pin,
                start=at(hour=7, minute=0, day=day),
                end=at(hour=8, minute=0, day=day),
            )
            for day in (16, 17)
        ]
    assert [[(d.trip_id, d.mode) for d in day] for day in found] == [
        [('NT31-feed', Mode.REGIONAL)],
        [('NT31-bundle', Mode.REGIONAL)],
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
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.departures(
            pin=Pin(origin='215020', destination='200060'),
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
