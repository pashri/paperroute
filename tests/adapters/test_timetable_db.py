"""Tests for the SQLite timetable repository."""

import dataclasses
import logging
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.sqlite_ro import file_identity
from paperroute.adapters.timetable_db import (
    LABEL_LIMIT,
    SqliteTimetableRepository,
)
from paperroute.build.dedupe import merge_rail
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.geo import to_metres
from paperroute.core.model import (
    SYDNEY,
    CarryOn,
    Departure,
    Mode,
    Pin,
    RouteKind,
    StopPlace,
    TripFacts,
)
from tests.build.feeds import (
    DAILY,
    STATIONS,
    build_db,
    bundle_rail_feed,
    bus_feed,
    regional_sources,
    shaped_bus_feed,
    stop_times,
    sydneytrains_feed,
)
from tests.build.network_feed import network_feed
from tests.core.factories import pin_of, place
from tests.helpers import (
    old_schema,
    schema_five,
    schema_four,
    schema_three,
    slice_db,
    write_feed,
)

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'
MONDAY: Final[date] = date(2026, 10, 19)
HOLLIS_TO_PARRA = pin_of('2145615', '215020')
PARRA_TO_CENTRAL = pin_of('215020', '200060')


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
    assert repo.stop(stop_id='215020').name == 'Parramatta Station'
    with pytest.raises(KeyError):
        repo.stop(stop_id='nope')


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
            pin=pin_of('200060', '215020'),
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
    pin = pin_of('200060', '215020')
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


def single_trip_db(*, directory: Path, trip: str) -> Path:
    """
    Build a timetable with one Parramatta to Central trip.

    Parameters
    ----------
    directory : Path
        Empty directory for the feed and database.
    trip : str
        Trip ID.

    Returns
    -------
    Path
        The database file.
    """
    directory.mkdir()
    feed = write_feed(
        path=directory / 'feed.zip',
        files={
            'stops.txt': STATIONS,
            'routes.txt': [
                {
                    'route_id': 'r1',
                    'agency_id': 'NSWTrains',
                    'route_short_name': 'T1',
                    'route_type': '2',
                }
            ],
            'calendar.txt': [{'service_id': 'daily', **DAILY}],
            'trips.txt': [
                {
                    'route_id': 'r1',
                    'service_id': 'daily',
                    'trip_id': trip,
                    'trip_headsign': 'Central',
                }
            ],
            'stop_times.txt': stop_times(
                trip=trip,
                times=[('2150411', '08:10:00'), ('2000321', '08:50:00')],
            ),
        },
    )
    return build_db(
        tmp_path=directory,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(name='sydneytrains', archive=feed)],
    )


def trip_ids(*, repo: SqliteTimetableRepository) -> list[str]:
    """
    Trip IDs found for Parramatta to Central on the morning of the 16th.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.

    Returns
    -------
    list[str]
        Trip IDs.
    """
    return [
        d.trip_id
        for d in repo.departures(
            pin=PARRA_TO_CENTRAL,
            start=at(hour=8, minute=0),
            end=at(hour=9, minute=0),
        )
    ]


def replace_with(*, live: Path, trip: str) -> None:
    """
    Build a timetable with one trip and move it over the live file.

    Parameters
    ----------
    live : Path
        File to replace.
    trip : str
        Trip ID in the new timetable.
    """
    built = single_trip_db(directory=live.parent / f'new-{trip}', trip=trip)
    staged = live.with_name('timetable.new')
    built.replace(staged)
    os.replace(staged, live)


def test_departures_reopens_after_file_replaced(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A rebuilt timetable moved over the file is seen by the next query."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with closing(SqliteTimetableRepository(path=live)) as repo:
        assert trip_ids(repo=repo) == ['A-0810']
        replace_with(live=live, trip='B-0810')
        with caplog.at_level(logging.INFO):
            assert trip_ids(repo=repo) == ['B-0810']
    assert [
        (record.levelno, record.getMessage()) for record in caplog.records
    ] == [(logging.INFO, 'timetable reopened')]


def test_departures_keeps_old_data_when_replacement_is_junk(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A replacement that isn't a timetable is refused with a warning."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with closing(SqliteTimetableRepository(path=live)) as repo:
        junk = live.with_name('timetable.new')
        junk.write_bytes(b'not a database' * 100)
        os.replace(junk, live)
        with caplog.at_level(logging.INFO):
            assert trip_ids(repo=repo) == ['A-0810']
            assert trip_ids(repo=repo) == ['A-0810']
    levels = {record.levelno for record in caplog.records}
    assert levels == {logging.WARNING}


def test_stop_reopens_after_file_replaced(tmp_path: Path) -> None:
    """stop() also reopens a replaced file."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with closing(SqliteTimetableRepository(path=live)) as repo:
        replace_with(live=live, trip='B-0810')
        assert repo.stop(stop_id='215020').name == 'Parramatta Station'
        assert repo.identity == file_identity(path=live)


def test_missing_file_keeps_old_connection(tmp_path: Path) -> None:
    """With the file deleted, queries still answer from the open file."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with closing(SqliteTimetableRepository(path=live)) as repo:
        live.unlink()
        assert trip_ids(repo=repo) == ['A-0810']


def test_injected_connection_never_reopens(tmp_path: Path) -> None:
    """A repository given a connection ignores the file on disk."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    conn = sqlite3.connect(f'file:{live}?mode=ro', uri=True)
    with closing(SqliteTimetableRepository(connection=conn)) as repo:
        replace_with(live=live, trip='B-0810')
        assert trip_ids(repo=repo) == ['A-0810']


def test_departures_threads_survive_replacement(tmp_path: Path) -> None:
    """Two querying threads see no error while the file is replaced."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    errors: list[BaseException] = []
    seen: set[str] = set()
    stop = threading.Event()

    def loop() -> None:
        try:
            while not stop.is_set():
                seen.update(trip_ids(repo=repo))
        except Exception as error:  # pylint: disable=broad-exception-caught
            errors.append(error)

    with closing(SqliteTimetableRepository(path=live)) as repo:
        threads = [threading.Thread(target=loop) for _ in range(2)]
        for thread in threads:
            thread.start()
        for index in range(5):
            replace_with(live=live, trip=f'B{index}-0810')
        stop.set()
        for thread in threads:
            thread.join()
        assert trip_ids(repo=repo) == ['B4-0810']
    assert not errors
    assert seen


HOLLIS = '2145615'
AROUND = date(2026, 10, 16)


@pytest.fixture(name='slice_repo', scope='module')
def _slice_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the real GTFS slice, with shapes.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the tests.
    """
    db = slice_db(tmp_path=tmp_path_factory.mktemp('slice'))
    with closing(SqliteTimetableRepository(path=db)) as repo:
        yield repo


def make_dir(*, path: Path) -> Path:
    """
    Create a directory.

    Parameters
    ----------
    path : Path
        Directory to create.

    Returns
    -------
    Path
        The same path.
    """
    path.mkdir()
    return path


def slice_departures(
    *, repo: SqliteTimetableRepository
) -> list[Departure]:
    """
    The 606 departures from Hollis St to Parramatta on the 16th.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.

    Returns
    -------
    list[Departure]
        Departures, earliest first.
    """
    return repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=0, minute=0),
        end=at(hour=23, minute=59),
    )


def test_stop_has_projected_point(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A stop's point is its stored lon and lat in metres."""
    found = slice_repo.stop(stop_id=HOLLIS)
    row = slice_repo.fetch(
        sql='SELECT name, lon, lat FROM stops WHERE stop_id = ?',
        params=(HOLLIS,),
    )[0]
    assert found.stop_id == HOLLIS
    assert found.name == row[0]
    lon, lat = (float(str(value)) for value in row[1:])
    assert found.point == to_metres(lon=lon, lat=lat)
    with pytest.raises(KeyError):
        slice_repo.stop(stop_id='nope')


def test_stop_without_coordinates_has_no_point(tmp_path: Path) -> None:
    """A stop with no lat or lon has no point."""
    bare = {
        'stop_id': 'X1',
        'stop_name': 'Nowhere',
        'location_type': '0',
        'parent_station': '',
        'stop_lat': '',
        'stop_lon': '',
    }
    archive = write_feed(
        path=tmp_path / 'bare.zip',
        files={
            'stops.txt': [bare],
            'routes.txt': [],
            'calendar.txt': [],
            'trips.txt': [],
            'stop_times.txt': [],
        },
    )
    db = build_db(
        tmp_path=tmp_path,
        start=AROUND,
        days=1,
        sources=[Source(name='bundle', archive=archive)],
    )
    with closing(SqliteTimetableRepository(path=db)) as repo:
        assert repo.stop(stop_id='X1') == StopPlace(
            stop_id='X1', name='Nowhere', point=None
        )


def test_departures_carry_shape_id(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Every 606 departure names a namespaced shape."""
    found = slice_departures(repo=slice_repo)
    assert found
    assert {d.route for d in found} == {'606'}
    assert all(
        d.shape_id and d.shape_id.startswith('bundle:') for d in found
    )


def test_shapes_returns_requested_only(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Only the asked-for shapes come back; unknown IDs are absent."""
    ids = {d.shape_id for d in slice_departures(repo=slice_repo)}
    first = sorted(str(i) for i in ids)[0]
    found = slice_repo.shapes(shape_ids=[first, 'bundle:nope'])
    assert list(found) == [first]
    assert found[first].shape_id == first
    assert len(found[first].points) >= 2
    assert slice_repo.shapes(shape_ids=[]) == {}


def test_patterns_resolve_alias_and_days(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """An aliased trip is keyed by the alias, with ordered stops."""
    alias = str(
        slice_repo.fetch(
            sql="""
            SELECT a.alias_id FROM trip_aliases a
            JOIN trips t ON t.trip_id = a.trip_id
            JOIN services sv ON sv.service_id = t.service_id
            WHERE sv.date = '2026-10-16' ORDER BY a.alias_id
            """,
            params=(),
        )[0][0]
    )
    found = slice_repo.patterns(trip_ids=[alias], around=AROUND)
    pattern = found[alias]
    assert pattern.trip_id == alias
    assert pattern.route == 'T1'
    assert pattern.mode == Mode.TRAIN
    times = [stop.arrives for stop in pattern.stops]
    assert times == sorted(times)
    assert len(times) > 2
    assert pattern.days
    assert set(pattern.days) <= {date(2026, 10, 15), AROUND}
    assert all(stop.point is not None for stop in pattern.stops)


def test_patterns_carry_stations(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Each call names its parent station, or the stop when it has none."""
    trips = [d.trip_id for d in slice_departures(repo=slice_repo)]
    found = slice_repo.patterns(trip_ids=trips, around=AROUND)
    stations = {stop.station for p in found.values() for stop in p.stops}
    parents = {
        str(row[0])
        for row in slice_repo.fetch(
            sql="SELECT stop_id FROM stops WHERE parent_station IS NULL "
            "OR parent_station = ''",
            params=(),
        )
    }
    assert f'G{HOLLIS}' in stations
    assert HOLLIS not in stations
    assert '215020' in stations
    assert stations <= parents


def test_patterns_name_each_calls_station(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Each call carries its station's name, not its platform's."""
    trips = [d.trip_id for d in slice_departures(repo=slice_repo)]
    found = slice_repo.patterns(trip_ids=trips, around=AROUND)
    names = {
        str(row[0]): str(row[1])
        for row in slice_repo.fetch(
            sql='SELECT stop_id, name FROM stops', params=(),
        )
    }
    stops = [stop for p in found.values() for stop in p.stops]
    assert stops
    assert all(stop.name == names[stop.station] for stop in stops)


def test_unknown_trip_pattern_is_absent(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A trip the timetable doesn't know has no pattern."""
    assert slice_repo.patterns(trip_ids=['nope'], around=AROUND) == {}


def test_route_labels_by_trip_and_alias(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Trips and aliases both map to their route's short name."""
    trip = slice_departures(repo=slice_repo)[0].trip_id
    alias = str(
        slice_repo.fetch(
            sql='SELECT alias_id FROM trip_aliases ORDER BY alias_id',
            params=(),
        )[0][0]
    )
    labels = slice_repo.route_labels(trip_ids=[trip, alias, 'nope'])
    assert labels == {trip: '606', alias: 'T1'}


def test_route_labels_cache_is_bounded(
    repo: SqliteTimetableRepository,
) -> None:
    """A cache past its limit is cleared before it takes more."""
    unknown = [f'x{i}' for i in range(LABEL_LIMIT + 1)]
    assert repo.route_labels(trip_ids=unknown) == {}
    assert len(repo.labels) == LABEL_LIMIT + 1
    assert repo.route_labels(trip_ids=['bus-0803']) == {'bus-0803': '606'}
    assert len(repo.labels) == 1


def record_queries(
    *, repo: SqliteTimetableRepository, monkeypatch: pytest.MonkeyPatch
) -> list[int]:
    """
    Record the parameter count of every parameterised query a repo runs.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to watch.
    monkeypatch : pytest.MonkeyPatch
        Restores the repository afterwards.

    Returns
    -------
    list[int]
        Filled as queries run.
    """
    seen: list[int] = []
    original = repo.fetch

    def fetch(
        *, sql: str, params: tuple[object, ...] | dict[str, object]
    ) -> list[tuple[object, ...]]:
        if params:
            seen.append(len(params))
        return original(sql=sql, params=params)

    monkeypatch.setattr(repo, 'fetch', fetch)
    return seen


def test_route_labels_query_in_chunks_of_500(
    repo: SqliteTimetableRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1200 IDs take three chunks per lookup, by trip and by alias."""
    seen = record_queries(repo=repo, monkeypatch=monkeypatch)
    repo.route_labels(trip_ids=[f't{i}' for i in range(1200)])
    assert sorted(seen) == [200, 200, 500, 500, 500, 500]


def test_route_labels_remember_unknown_ids(
    repo: SqliteTimetableRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown ID is asked about once, not on every poll."""
    seen = record_queries(repo=repo, monkeypatch=monkeypatch)
    assert repo.route_labels(trip_ids=['nope', 'bus-0803']) == {
        'bus-0803': '606'
    }
    first = len(seen)
    assert repo.route_labels(trip_ids=['nope', 'bus-0803']) == {
        'bus-0803': '606'
    }
    assert first > 0
    assert len(seen) == first


def test_route_labels_reset_when_file_replaced(tmp_path: Path) -> None:
    """A label cached from the old file is not served from the new one."""
    live = build_db(
        tmp_path=make_dir(path=tmp_path / 'a'),
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(
                name='bundle',
                archive=bus_feed(path=tmp_path / 'b.zip'),
            )
        ],
    )
    with closing(SqliteTimetableRepository(path=live)) as repo:
        assert repo.route_labels(trip_ids=['bus-0803']) == {
            'bus-0803': '606'
        }
        replace_with(live=live, trip='B-0810')
        assert repo.route_labels(trip_ids=['bus-0803']) == {}
        assert repo.route_labels(trip_ids=['B-0810']) == {'B-0810': 'T1'}


def test_patterns_days_cover_the_day_before(
    repo: SqliteTimetableRepository,
) -> None:
    """Dates are the asked-for day and the day before, where they run."""
    def days(*, day: int) -> tuple[date, ...]:
        found = repo.patterns(
            trip_ids=['bus-0803'], around=date(2026, 10, day)
        )
        return found['bus-0803'].days

    assert days(day=16) == (date(2026, 10, 15), date(2026, 10, 16))
    assert days(day=18) == (date(2026, 10, 17),)
    assert days(day=20) == ()


def test_patterns_days_leave_out_skipped_dates(
    repo: SqliteTimetableRepository,
) -> None:
    """A date the trip is skipped on is not one of its days."""
    assert repo.db.path is not None
    with closing(sqlite3.connect(repo.db.path)) as conn:
        conn.execute(
            "INSERT INTO trip_skip VALUES ('bus-0803', '2026-10-16')"
        )
        conn.commit()
    found = repo.patterns(trip_ids=['bus-0803'], around=AROUND)
    assert found['bus-0803'].days == (date(2026, 10, 15),)


def test_patterns_partial_bundle_trip_shares_the_feed_days(
    tmp_path: Path,
) -> None:
    """A bundle ID covers the dates its feed twin runs as well as its own."""
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
    day = date(2026, 10, 17)
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.patterns(
            trip_ids=['NT31-bundle', 'NT31-feed'], around=day
        )
    assert found['NT31-bundle'].days == (AROUND, day)
    assert found['NT31-feed'].days == (AROUND,)
    assert found['NT31-bundle'].stops[-1].departs == 7 * 3600 + 40 * 60 + 30
    assert found['NT31-feed'].stops[-1].departs == 7 * 3600 + 40 * 60


def test_patterns_alias_runs_whenever_any_of_its_trips_do(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """An alias of two trips takes the days of both, not just the first."""
    alias = 'NP43.947.121026.124.P.2.91437177'
    first = date(2026, 10, 16)
    second = date(2026, 10, 17)
    assert first in slice_repo.patterns(trip_ids=[alias], around=first)[
        alias
    ].days
    found = slice_repo.patterns(trip_ids=[alias], around=second)[alias]
    assert found.days == (first, second)
    assert found.stops


def test_modes_near_finds_bus_at_the_606_stop(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A box around the 606 stop holds buses; one offshore holds nothing."""
    x, y = slice_repo.stop(stop_id=HOLLIS).point or (0.0, 0.0)
    box = (x - 2000.0, y - 2000.0, x + 2000.0, y + 2000.0)
    assert Mode.BUS in slice_repo.modes_near(box=box)
    sx, sy = to_metres(lon=155.0, lat=-33.0)
    far = (sx - 2000.0, sy - 2000.0, sx + 2000.0, sy + 2000.0)
    assert slice_repo.modes_near(box=far) == frozenset()


def test_old_schema_timetable_still_answers(tmp_path: Path) -> None:
    """A timetable without shapes answers departures and empty maps."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(
                name='bundle',
                archive=bus_feed(path=tmp_path / 'b.zip'),
            )
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('ALTER TABLE trips DROP COLUMN shape_id')
        conn.execute('DROP TABLE shapes')
        conn.commit()
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.departures(
            pin=HOLLIS_TO_PARRA,
            start=at(hour=8, minute=0),
            end=at(hour=9, minute=0),
        )
        assert [d.shape_id for d in found] == [None]
        assert repo.shapes(shape_ids=['bundle:x']) == {}
        assert repo.modes_near(box=(-1e6, -1e6, 1e6, 1e6)) == frozenset()
        pattern = repo.patterns(trip_ids=['bus-0803'], around=AROUND)
        assert pattern['bus-0803'].shape_id is None


def test_old_schema_checked_after_reopen(tmp_path: Path) -> None:
    """Shapes are read from a replacement file with the new schema."""
    old = build_db(
        tmp_path=make_dir(path=tmp_path / 'old'),
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(
                name='bundle',
                archive=bus_feed(path=tmp_path / 'b.zip'),
            )
        ],
    )
    with closing(sqlite3.connect(old)) as conn:
        conn.execute('ALTER TABLE trips DROP COLUMN shape_id')
        conn.execute('DROP TABLE shapes')
        conn.commit()
    new = build_db(
        tmp_path=make_dir(path=tmp_path / 'new'),
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(
                name='bundle',
                archive=shaped_bus_feed(
                    path=tmp_path / 's.zip',
                    points=[(150.98, -33.78), (151.0, -33.82)],
                ),
            )
        ],
    )
    with closing(SqliteTimetableRepository(path=old)) as repo:
        assert repo.shapes(shape_ids=['bundle:S1']) == {}
        os.replace(new, old)
        assert list(repo.shapes(shape_ids=['bundle:S1'])) == ['bundle:S1']


@pytest.fixture(name='network_db', scope='module')
def _network_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network feed, with its run tables, over a 3-day window.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file.
    """
    folder = tmp_path_factory.mktemp('network')
    out = folder / 'timetable.db'
    build_timetable(
        sources=[
            Source(
                name='sydneytrains',
                archive=network_feed(path=folder / 'network.zip'),
            ),
        ],
        window=Window(start=date(2026, 10, 5), days=3),
        out=out,
    )
    return out


@pytest.fixture(name='network')
def _network(network_db: Path) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the test.
    """
    with closing(SqliteTimetableRepository(path=network_db)) as repo:
        yield repo


@pytest.fixture(name='old_network')
def _old_network(
    network_db: Path, tmp_path: Path,
) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over a schema-2 copy of the network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.
    tmp_path : Path
        Working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the test.
    """
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    old_schema(path=copy)
    with closing(SqliteTimetableRepository(path=copy)) as repo:
        yield repo


def whole_stop(
    *,
    repo: SqliteTimetableRepository,
    origin: str,
    hours: tuple[int, int] = (7, 8),
) -> list[Departure]:
    """
    Departures from a network stop with no Destination on 6 October.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.
    origin : str
        From stop or station ID.
    hours : tuple[int, int]
        First and last hour of the range, from the hour to its 59th minute.

    Returns
    -------
    list[Departure]
        Departures, earliest first.
    """
    return repo.departures(
        pin=Pin(
            origin=place(origin),
            kind=RouteKind.TRAIN,
            routes=('T2',),
        ),
        start=at(hour=hours[0], minute=0, day=6),
        end=at(hour=hours[1], minute=59, day=6),
    )


def test_no_destination_lists_the_whole_stop(
    network: SqliteTimetableRepository,
) -> None:
    """Every train route leaving C is listed, whatever routes were chosen."""
    found = whole_stop(repo=network, origin='C', hours=(7, 7))
    assert [d.trip_id for d in found] == [
        'ew1', 'we1', 'er1', 'cc1', 're1', 'y1', 'cc1',
    ]
    assert {d.route for d in found} == {'T1', 'T2'}
    assert [d.departs for d in found] == sorted(d.departs for d in found)


def test_no_destination_covers_every_platform(tmp_path: Path) -> None:
    """Departures from two platforms of one station both appear."""
    stops = [
        {'stop_id': 'P', 'stop_name': 'P Station', 'location_type': '1',
         'parent_station': '', 'stop_lat': '-33.8', 'stop_lon': '151.0'},
        *(
            {'stop_id': f'P_{n}', 'stop_name': f'P Station, Platform {n}',
             'location_type': '0', 'parent_station': 'P',
             'stop_lat': '-33.8', 'stop_lon': '151.0'}
            for n in (1, 2)
        ),
        {'stop_id': 'Q', 'stop_name': 'Q Station', 'location_type': '0',
         'parent_station': '', 'stop_lat': '-33.9', 'stop_lon': '151.1'},
    ]
    archive = write_feed(
        path=tmp_path / 'p.zip',
        files={
            'stops.txt': stops,
            'routes.txt': [{'route_id': 'T1', 'agency_id': 'SydneyTrains',
                            'route_short_name': 'T1',
                            'route_long_name': 'Line T1',
                            'route_type': '2'}],
            'calendar.txt': [{'service_id': 'daily', **DAILY}],
            'trips.txt': [
                {'route_id': 'T1', 'service_id': 'daily', 'trip_id': trip,
                 'trip_headsign': 'Q', 'direction_id': '0',
                 'block_id': ''}
                for trip in ('a', 'b')
            ],
            'stop_times.txt': [
                *stop_times(trip='a', times=[('P_1', '08:00:00'),
                                             ('Q', '08:10:00')]),
                *stop_times(trip='b', times=[('P_2', '08:05:00'),
                                             ('Q', '08:15:00')]),
            ],
        },
    )
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(name='sydneytrains', archive=archive)],
    )
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.departures(
            pin=pin_of('P', None),
            start=at(hour=8, minute=0),
            end=at(hour=9, minute=0),
        )
    assert [(d.trip_id, d.platform, d.facts.then) for d in found] == [
        ('a', '1', None),
        ('b', '2', None),
    ]


def test_no_destination_runs_to_the_last_stop(
    network: SqliteTimetableRepository,
) -> None:
    """A C to W2 trip runs to its last call, at W2."""
    found = whole_stop(repo=network, origin='C', hours=(7, 7))[0]
    assert (found.trip_id, found.origin_stop) == ('ew1', 'C_1')
    assert (found.destination_seq, found.destination_stop) == (6, 'W2_1')
    assert found.arrives == at(hour=7, minute=15, day=6)


def test_no_destination_skips_trips_ending_here(
    network: SqliteTimetableRepository,
) -> None:
    """From W2, only the trips leaving W2 are listed, not those ending."""
    found = whole_stop(repo=network, origin='W2', hours=(0, 23))
    assert [d.trip_id for d in found] == [
        'we1', 'y2', 'we2', 'we3', 'we4', 'we5', 'we6',
    ]


def test_departures_carry_their_kind(
    network: SqliteTimetableRepository,
) -> None:
    """T1 departures are Train, with or without a Destination."""
    to_w2 = network.departures(
        pin=pin_of('C', 'W2'),
        start=at(hour=7, minute=0, day=6),
        end=at(hour=7, minute=10, day=6),
    )
    anywhere = whole_stop(repo=network, origin='C', hours=(7, 7))
    assert to_w2[0].facts.kind is RouteKind.TRAIN
    assert anywhere[0].facts.kind is RouteKind.TRAIN


def test_departures_carry_on_as_the_next_route(
    network: SqliteTimetableRepository,
    old_network: SqliteTimetableRepository,
) -> None:
    """v1 carries on as the 811; an old timetable doesn't know."""
    then = CarryOn(route='811', headsign='Parramatta Stn', trip_id='v2')
    for pin in (pin_of('B1', None), pin_of('B1', 'B2')):
        found = network.departures(
            pin=pin,
            start=at(hour=9, minute=0, day=6),
            end=at(hour=10, minute=0, day=6),
        )
        assert [(d.trip_id, d.facts) for d in found] == [(
            'v1',
            TripFacts(
                kind=RouteKind.BUS, then=then, route_id='sydneytrains:809',
            ),
        )]
    old = old_network.departures(
        pin=pin_of('B1', None),
        start=at(hour=9, minute=0, day=6),
        end=at(hour=10, minute=0, day=6),
    )
    assert [(d.trip_id, d.facts.kind, d.facts.then) for d in old] == [
        ('v1', RouteKind.BUS, None),
    ]


def test_carry_on_without_next_trip_column(
    network_db: Path, tmp_path: Path,
) -> None:
    """A schema-3 file still carries v1 on as the 811, with no trip."""
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    schema_three(path=copy)
    with closing(SqliteTimetableRepository(path=copy)) as repo:
        found = repo.departures(
            pin=pin_of('B1', None),
            start=at(hour=9, minute=0, day=6),
            end=at(hour=10, minute=0, day=6),
        )
    assert [(d.trip_id, d.facts.then) for d in found] == [
        ('v1', CarryOn(route='811', headsign='Parramatta Stn', trip_id='')),
    ]


def test_old_schema_lists_the_whole_stop(
    network: SqliteTimetableRepository,
    old_network: SqliteTimetableRepository,
) -> None:
    """A schema-2 file lists the same whole stop, without carry-ons."""
    new = whole_stop(repo=network, origin='C', hours=(0, 23))
    old = whole_stop(repo=old_network, origin='C', hours=(0, 23))
    assert old == [
        dataclasses.replace(
            departure,
            facts=dataclasses.replace(departure.facts, then=None),
        )
        for departure in new
    ]
    assert len(old) > 7


def require_real_monday() -> None:
    """
    Skip the test unless the real timetable covers Monday 19 October 2026.

    The file must exist and hold the run tables.
    """
    if not REAL_DB.exists():
        pytest.skip('no real timetable')
    with closing(sqlite3.connect(REAL_DB)) as conn:
        tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'trip_next'",
        ).fetchall()
        meta = dict(conn.execute('SELECT key, value FROM meta').fetchall())
    if not tables:
        pytest.skip('real timetable has no run tables')
    start = date.fromisoformat(meta['window_start'])
    if not 0 <= (MONDAY - start).days < int(meta['window_days']):
        pytest.skip('real timetable does not cover 19 October 2026')


def real_809_stops() -> list[str]:
    """
    Find Merrylands Rd Near Bambil St in the real timetable.

    Skips the test when the file is missing, has no run tables or does not
    cover Monday 19 October 2026.

    Returns
    -------
    list[str]
        Stop IDs with that name.
    """
    require_real_monday()
    with closing(sqlite3.connect(REAL_DB)) as conn:
        stops = conn.execute(
            'SELECT stop_id FROM stops WHERE name = ?',
            ('Merrylands Rd Near Bambil St',),
        ).fetchall()
    return [str(stop) for stop, in stops]


def test_809_carries_on_as_811_at_merrylands() -> None:
    """20 of the 46 809s carry on as the 811; the 9:04 and 15:29 don't."""
    stops = real_809_stops()
    with closing(SqliteTimetableRepository(path=REAL_DB)) as repo:
        found = [
            departure
            for stop in stops
            for departure in repo.departures(
                pin=pin_of(stop, None),
                start=datetime(2026, 10, 19, tzinfo=SYDNEY),
                end=datetime(2026, 10, 19, 23, 59, 59, tzinfo=SYDNEY),
            )
            if departure.route == '809'
        ]
    carried = [
        d
        for d in found
        if d.facts.then is not None
        and (d.facts.then.route, d.facts.then.headsign)
        == ('811', 'Parramatta Stn')
    ]
    on = {d.departs.strftime('%H:%M') for d in carried}
    assert len(found) == 46
    assert len(carried) == 20
    assert all(d.facts.then and d.facts.then.trip_id for d in carried)
    assert not on & {'09:04', '15:29'}
    assert {'09:04', '15:29'} <= {d.departs.strftime('%H:%M') for d in found}


def test_version_reads_built_at(
    network: SqliteTimetableRepository, network_db: Path, tmp_path: Path,
) -> None:
    """The version is the build time; a file without one has None."""
    with closing(sqlite3.connect(network_db)) as conn:
        built_at = conn.execute(
            "SELECT value FROM meta WHERE key = 'built_at'",
        ).fetchone()[0]
    assert network.version() == built_at
    bare = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=1,
        sources=[
            Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
        ],
    )
    with closing(SqliteTimetableRepository(path=bare)) as repo:
        assert repo.version() is None


def test_match_stop_same_id_refreshes_name_and_point(
    network: SqliteTimetableRepository,
) -> None:
    """A place whose ID is still there comes back as the timetable has it."""
    stale = StopPlace(stop_id='B1', name='Old Name', point=None)
    assert network.match_stop(place=stale) == network.stop(stop_id='B1')


def moved_b1(
    *,
    network: SqliteTimetableRepository,
    east: float,
    name: str,
    north: float = 0.0,
) -> StopPlace:
    """
    A place with a retired ID, a distance east of B1.

    Parameters
    ----------
    network : SqliteTimetableRepository
        Network repository.
    east : float
        Metres east of B1.
    name : str
        Name of the place.
    north : float
        Metres north of B1.

    Returns
    -------
    StopPlace
        The place.
    """
    point = network.stop(stop_id='B1').point
    assert point is not None
    return StopPlace(
        stop_id='retired',
        name=name,
        point=(point[0] + east, point[1] + north),
    )


def test_match_stop_by_name_within_100_m(
    network: SqliteTimetableRepository,
) -> None:
    """A renamed ID 60 m away is found by its name."""
    place_60 = moved_b1(network=network, east=60.0, name='B1 Stop')
    assert network.match_stop(place=place_60) == network.stop(stop_id='B1')


def test_match_stop_too_far_or_renamed_is_none(
    network: SqliteTimetableRepository,
) -> None:
    """150 m away, 113 m diagonally, another name or no point gives None."""
    far = moved_b1(network=network, east=150.0, name='B1 Stop')
    corner = moved_b1(network=network, east=80.0, name='B1 Stop', north=80.0)
    renamed = moved_b1(network=network, east=60.0, name='B2 Stop')
    pointless = StopPlace(stop_id='retired', name='B1 Stop', point=None)
    for lost in (far, corner, renamed, pointless):
        assert network.match_stop(place=lost) is None


def edited_network(
    *, network_db: Path, tmp_path: Path, script: str,
) -> SqliteTimetableRepository:
    """
    Open a copy of the network timetable after running SQL on it.

    Parameters
    ----------
    network_db : Path
        Timetable file.
    tmp_path : Path
        Working directory.
    script : str
        SQL statements to run on the copy.

    Returns
    -------
    SqliteTimetableRepository
        Repository over the copy; the caller closes it.
    """
    copy = tmp_path / 'edited.db'
    shutil.copy(network_db, copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.executescript(script)
        conn.commit()
    return SqliteTimetableRepository(path=copy)


def test_no_destination_skips_no_pickup_and_skipped_trips(
    network_db: Path, tmp_path: Path,
) -> None:
    """ew1 has no pickup at C and we1 is skipped on the 6th."""
    script = (
        "UPDATE stop_times SET pickup = 1 "
        "WHERE trip_id = 'ew1' AND stop_id = 'C_1';"
        "INSERT INTO trip_skip VALUES ('we1', '2026-10-06');"
    )
    with closing(
        edited_network(network_db=network_db, tmp_path=tmp_path, script=script)
    ) as repo:
        found = whole_stop(repo=repo, origin='C', hours=(7, 7))
    assert [d.trip_id for d in found] == ['er1', 'cc1', 're1', 'y1', 'cc1']


def test_departures_at_the_same_time_sort_by_trip(
    network_db: Path, tmp_path: Path,
) -> None:
    """A late trip from the day before at ew1's time sorts after ew1."""
    late = 31 * 3600 + 6 * 60  # 07:06 the next morning
    script = (
        "INSERT INTO trips SELECT 'zz', route_id, service_id, headsign, "
        'direction_id, block_id, agency_id, source, shape_id, service '
        "FROM trips WHERE trip_id = 'ew1';"
        f"INSERT INTO stop_times VALUES ('zz', 1, 'C_1', {late}, {late}, 0, 0),"
        f"('zz', 2, 'J_1', {late + 180}, {late + 180}, 0, 0);"
    )
    with closing(
        edited_network(network_db=network_db, tmp_path=tmp_path, script=script)
    ) as repo:
        found = whole_stop(repo=repo, origin='C', hours=(7, 7))
    assert [(d.trip_id, d.service_day.day) for d in found[:2]] == [
        ('ew1', 6),
        ('zz', 5),
    ]


def test_train_pin_to_blacktown_parramatta_lists_only_trains() -> None:
    """The 8:09 706 bus between the stations' stands is not listed."""
    require_real_monday()
    with closing(SqliteTimetableRepository(path=REAL_DB)) as repo:
        pin = Pin(
            origin=repo.stop(stop_id='214810'),
            destination=repo.stop(stop_id='215020'),
            kind=RouteKind.TRAIN,
            routes=('T1',),
        )
        found = repo.departures(
            pin=pin,
            start=datetime(2026, 10, 19, 8, 0, tzinfo=SYDNEY),
            end=datetime(2026, 10, 19, 8, 30, tzinfo=SYDNEY),
        )
    assert found
    assert {d.facts.kind for d in found} == {RouteKind.TRAIN}
    assert all(d.arrives - d.departs < timedelta(minutes=30) for d in found)


def stand_feed(*, path: Path) -> Path:
    """
    Write stations P and Q, each with a platform and a bus stand.

    Parameters
    ----------
    path : Path
        Feed zip to write.

    Returns
    -------
    Path
        The feed: train `a` P_1 8:00 to Q_1 8:10, 706 bus `b` P_B 8:02 to
        Q_B 8:50 and 606 bus `c` P_B 8:04 to Q_B 8:30.
    """
    stops = [
        {'stop_id': key, 'stop_name': f'{key} Station',
         'location_type': '1', 'parent_station': '',
         'stop_lat': lat, 'stop_lon': '151.0'}
        for key, lat in (('P', '-33.8'), ('Q', '-33.9'))
    ] + [
        {'stop_id': f'{key}_{kind}', 'stop_name': f'{key} Station {kind}',
         'location_type': '0', 'parent_station': key,
         'stop_lat': lat, 'stop_lon': '151.0'}
        for key, lat in (('P', '-33.8'), ('Q', '-33.9'))
        for kind in ('1', 'B')
    ]
    return write_feed(
        path=path,
        files={
            'stops.txt': stops,
            'routes.txt': [
                {'route_id': route, 'agency_id': 'SydneyTrains',
                 'route_short_name': route, 'route_long_name': route,
                 'route_type': kind}
                for route, kind in (
                    ('T1', '2'), ('706', '700'), ('606', '700'),
                )
            ],
            'calendar.txt': [{'service_id': 'daily', **DAILY}],
            'trips.txt': [
                {'route_id': route, 'service_id': 'daily', 'trip_id': trip,
                 'trip_headsign': 'Q', 'direction_id': '0',
                 'block_id': ''}
                for trip, route in (('a', 'T1'), ('b', '706'), ('c', '606'))
            ],
            'stop_times.txt': [
                *stop_times(trip='a', times=[('P_1', '08:00:00'),
                                             ('Q_1', '08:10:00')]),
                *stop_times(trip='b', times=[('P_B', '08:02:00'),
                                             ('Q_B', '08:50:00')]),
                *stop_times(trip='c', times=[('P_B', '08:04:00'),
                                             ('Q_B', '08:30:00')]),
            ],
        },
    )


def test_destination_keeps_the_pins_kind(tmp_path: Path) -> None:
    """A Train pin to Q lists the train, not the bus between the stands."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(
            name='sydneytrains', archive=stand_feed(path=tmp_path / 'p.zip'),
        )],
    )
    window = {'start': at(hour=8, minute=0), 'end': at(hour=9, minute=0)}
    train = Pin(
        origin=place('P'), destination=place('Q'), kind=RouteKind.TRAIN,
    )
    with closing(SqliteTimetableRepository(path=db)) as repo:
        trains = repo.departures(pin=train, **window)
        every = repo.departures(pin=pin_of('P', 'Q'), **window)
    assert [d.trip_id for d in trains] == ['a']
    assert [d.trip_id for d in every] == ['a', 'b', 'c']


def test_whole_stop_board_only_pin_kind(tmp_path: Path) -> None:
    """A Train pin's board leaves out the stand's buses; no kind keeps all."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(
            name='sydneytrains', archive=stand_feed(path=tmp_path / 'p.zip'),
        )],
    )
    window = {'start': at(hour=8, minute=0), 'end': at(hour=9, minute=0)}
    train = Pin(origin=place('P'), kind=RouteKind.TRAIN, routes=('T1',))
    with closing(SqliteTimetableRepository(path=db)) as repo:
        trains = repo.departures(pin=train, **window)
        every = repo.departures(
            pin=dataclasses.replace(train, kind=None, routes=()), **window,
        )
    assert [d.trip_id for d in trains] == ['a']
    assert [d.trip_id for d in every] == ['a', 'b', 'c']


def test_destination_keeps_the_pins_bus_routes(tmp_path: Path) -> None:
    """A 606 Bus pin to Q lists the 606, not the 706 or the train."""
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(
            name='sydneytrains', archive=stand_feed(path=tmp_path / 'p.zip'),
        )],
    )
    window = {'start': at(hour=8, minute=0), 'end': at(hour=9, minute=0)}
    bus = Pin(
        origin=place('P'),
        destination=place('Q'),
        kind=RouteKind.BUS,
        routes=('606',),
    )
    with closing(SqliteTimetableRepository(path=db)) as repo:
        found = repo.departures(pin=bus, **window)
    assert [d.trip_id for d in found] == ['c']


def platform_repo(
    *, tmp_path: Path, trips: list[tuple[str, str, list[tuple[str, str]]]],
) -> SqliteTimetableRepository:
    """
    Build a timetable with station P (platforms P_1, P_2) and stop Q.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    trips : list[tuple[str, str, list[tuple[str, str]]]]
        Each trip's ID, headsign and calls.

    Returns
    -------
    SqliteTimetableRepository
        Repository over the built file; the caller closes it.
    """
    stops = [
        {'stop_id': 'P', 'stop_name': 'P Station', 'location_type': '1',
         'parent_station': '', 'stop_lat': '-33.8', 'stop_lon': '151.0'},
        *(
            {'stop_id': f'P_{n}', 'stop_name': f'P Station, Platform {n}',
             'location_type': '0', 'parent_station': 'P',
             'stop_lat': '-33.8', 'stop_lon': '151.0'}
            for n in (1, 2)
        ),
        {'stop_id': 'Q', 'stop_name': 'Q Station', 'location_type': '0',
         'parent_station': '', 'stop_lat': '-33.9', 'stop_lon': '151.1'},
    ]
    archive = write_feed(
        path=tmp_path / 'p.zip',
        files={
            'stops.txt': stops,
            'routes.txt': [{'route_id': 'T1', 'agency_id': 'SydneyTrains',
                            'route_short_name': 'T1',
                            'route_long_name': 'Line T1',
                            'route_type': '2'}],
            'calendar.txt': [{'service_id': 'daily', **DAILY}],
            'trips.txt': [
                {'route_id': 'T1', 'service_id': 'daily', 'trip_id': trip,
                 'trip_headsign': headsign, 'direction_id': '0',
                 'block_id': ''}
                for trip, headsign, _ in trips
            ],
            'stop_times.txt': [
                row
                for trip, _, times in trips
                for row in stop_times(trip=trip, times=times)
            ],
        },
    )
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(name='sydneytrains', archive=archive)],
    )
    return SqliteTimetableRepository(path=db)


def p_departures(
    *, repo: SqliteTimetableRepository, destination: str | None,
) -> list[tuple[str, datetime]]:
    """
    List the trips and times leaving P from 8:00 to 9:00.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository.
    destination : str | None
        Destination, or None for the whole stop.

    Returns
    -------
    list[tuple[str, datetime]]
        Trip ID and departure time of each departure.
    """
    found = repo.departures(
        pin=pin_of('P', destination),
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=0),
    )
    return [(d.trip_id, d.departs) for d in found]


def test_empty_running_trips_are_never_listed(tmp_path: Path) -> None:
    """An Empty Train is left off the whole-stop and Destination boards."""
    trips = [
        ('a', 'Q', [('P_1', '08:00:00'), ('Q', '08:10:00')]),
        ('e', 'Empty Train', [('P_2', '08:05:00'), ('Q', '08:15:00')]),
    ]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('a', at(hour=8, minute=0)),
            ]


def test_consecutive_calls_within_the_from_are_one_departure(
    tmp_path: Path,
) -> None:
    """A trip moving between two platforms of the From leaves once.

    It is listed at its last call there, when it leaves the place; a
    trip that comes back after another stop is listed at both visits.
    """
    trips = [
        ('two', 'Q', [('P_1', '08:00:00'), ('P_2', '08:05:00'),
                      ('Q', '08:10:00')]),
        ('loop', 'Q', [('P_1', '08:20:00'), ('Q', '08:30:00'),
                       ('P_2', '08:40:00'), ('Q', '08:50:00')]),
    ]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('two', at(hour=8, minute=5)),
                ('loop', at(hour=8, minute=20)),
                ('loop', at(hour=8, minute=40)),
            ]


def test_consecutive_calls_keep_the_last_with_pickup(
    tmp_path: Path,
) -> None:
    """A later call at the From with no pickup leaves the earlier one."""
    trips = [
        ('two', 'Q', [('P_1', '08:00:00'), ('P_2', '08:05:00'),
                      ('Q', '08:10:00')]),
    ]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        with closing(sqlite3.connect(tmp_path / 'timetable.db')) as conn:
            conn.execute(
                "UPDATE stop_times SET pickup = 1 "
                "WHERE trip_id = 'two' AND stop_id = 'P_2'",
            )
            conn.commit()
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('two', at(hour=8, minute=0)),
            ]


def empty_running_repo(*, tmp_path: Path) -> SqliteTimetableRepository:
    """
    Build P to Q with one revenue trip and one out-of-service trip.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Returns
    -------
    SqliteTimetableRepository
        Repository over the built file; the caller closes it.
    """
    calls = [('P_1', '08:00:00'), ('Q', '08:10:00')]
    repo = platform_repo(
        tmp_path=tmp_path,
        trips=[('revenue', 'Q', calls), ('empty', 'Q', calls)],
    )
    with closing(sqlite3.connect(tmp_path / 'timetable.db')) as conn:
        conn.execute(
            "UPDATE trips SET service = 0 WHERE trip_id = 'empty'",
        )
        conn.commit()
    return repo


def test_departures_skip_non_service(tmp_path: Path) -> None:
    """An out-of-service trip is not listed, with or without a Destination."""
    with closing(empty_running_repo(tmp_path=tmp_path)) as repo:
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('revenue', at(hour=8, minute=0)),
            ]


def test_route_labels_include_non_service(tmp_path: Path) -> None:
    """The Map still labels an out-of-service trip."""
    with closing(empty_running_repo(tmp_path=tmp_path)) as repo:
        found = repo.route_labels(trip_ids={'empty'})
    assert found == {'empty': 'T1'}


def test_patterns_include_non_service(tmp_path: Path) -> None:
    """The Map still gets the pattern of an out-of-service trip."""
    with closing(empty_running_repo(tmp_path=tmp_path)) as repo:
        found = repo.patterns(trip_ids={'empty'}, around=MONDAY)
    assert found['empty'].route == 'T1'
    assert len(found['empty'].stops) == 2


def test_departures_on_schema_four_file(tmp_path: Path) -> None:
    """A file without the service column still gives departures."""
    empty_running_repo(tmp_path=tmp_path).close()
    schema_four(path=tmp_path / 'timetable.db')
    with closing(
        SqliteTimetableRepository(path=tmp_path / 'timetable.db')
    ) as old:
        for destination in (None, 'Q'):
            assert [
                trip
                for trip, _ in p_departures(repo=old, destination=destination)
            ] == ['empty', 'revenue']


def e2_to_w1(*, repo: SqliteTimetableRepository) -> list[tuple[str, bool]]:
    """
    The network's departures from E2 to W1 on 6 October, 7 to 9 am.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.

    Returns
    -------
    list[tuple[str, bool]]
        Trip ID and whether it carries on into another trip, in order.
    """
    found = repo.departures(
        pin=pin_of('E2', 'W1'),
        start=at(hour=7, minute=0, day=6),
        end=at(hour=8, minute=59, day=6),
    )
    return [(d.trip_id, d.facts.onto is not None) for d in found]


def test_departures_add_carry_ons(
    network: SqliteTimetableRepository,
) -> None:
    """x1 reaches W1 only by running on as x2."""
    assert ('x1', True) in e2_to_w1(repo=network)


def test_departures_without_trip_joins(
    network_db: Path, tmp_path: Path,
) -> None:
    """A schema-5 file lists only direct trips, without an error."""
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    schema_five(path=copy)
    with closing(SqliteTimetableRepository(path=copy)) as repo:
        found = e2_to_w1(repo=repo)
    assert found
    assert all(not onto for _, onto in found)
    assert 'x1' not in [trip for trip, _ in found]


def routes_of(
    *,
    repo: SqliteTimetableRepository,
    pin: Pin,
) -> set[str]:
    """
    Route names of a pin's departures on 6 October.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.
    pin : Pin
        The pin.

    Returns
    -------
    set[str]
        Route short names.
    """
    return {
        departure.route
        for departure in repo.departures(
            pin=pin,
            start=at(hour=0, minute=0, day=6),
            end=at(hour=23, minute=59, day=6),
        )
    }


def test_departures_respect_filter(
    network: SqliteTimetableRepository,
) -> None:
    """A route filter narrows the board; an empty one keeps both lines."""
    pin = Pin(
        origin=place('C'),
        kind=RouteKind.TRAIN,
        routes=('T1',),
        only=('T2',),
    )
    assert routes_of(repo=network, pin=pin) == {'T2'}
    assert routes_of(repo=network, pin=replace(pin, only=())) == {'T1', 'T2'}


def test_filter_with_destination(
    network: SqliteTimetableRepository,
) -> None:
    """From C to J, the filter keeps only the chosen line."""
    pin = Pin(
        origin=place('C'),
        destination=place('J'),
        kind=RouteKind.TRAIN,
        routes=(),
    )
    assert routes_of(repo=network, pin=pin) == {'T1', 'T2'}
    only = replace(pin, only=('T1',))
    assert routes_of(repo=network, pin=only) == {'T1'}


def test_filter_matches_carry_on_by_boarding_route(
    network: SqliteTimetableRepository,
) -> None:
    """The 809 carrying on as the 811 is kept by 809, dropped by 811."""
    pin = pin_of('B1', 'B2')
    assert routes_of(repo=network, pin=pin) == {'809'}
    kept = replace(pin, only=('809',))
    assert routes_of(repo=network, pin=kept) == {'809'}
    dropped = replace(pin, only=('811',))
    assert routes_of(repo=network, pin=dropped) == set()


def test_departures_carry_their_route_id(
    network: SqliteTimetableRepository,
) -> None:
    """Every departure names the timetable route its trip runs on."""
    to_w2 = network.departures(
        pin=pin_of('C', 'W2'),
        start=at(hour=7, minute=0, day=6),
        end=at(hour=7, minute=10, day=6),
    )
    anywhere = whole_stop(repo=network, origin='C', hours=(7, 7))
    carried = network.departures(
        pin=pin_of('B1', 'B2'),
        start=at(hour=9, minute=0, day=6),
        end=at(hour=10, minute=0, day=6),
    )
    assert {d.facts.route_id for d in (*to_w2, *anywhere)} >= {
        'sydneytrains:T1',
    }
    assert all(
        d.facts.route_id.endswith(d.route) for d in (*anywhere, *carried)
    )
    assert carried
