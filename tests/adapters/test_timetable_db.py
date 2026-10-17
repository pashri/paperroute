"""Tests for the SQLite timetable repository."""

import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from paperroute.adapters.sqlite_ro import file_identity
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.dedupe import merge_rail
from paperroute.build.timetable import Source
from paperroute.core.geo import to_metres
from paperroute.core.model import SYDNEY, Departure, Mode, Pin, StopPlace
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


def test_stop_name_reopens_after_file_replaced(tmp_path: Path) -> None:
    """stop_name() also reopens a replaced file."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with closing(SqliteTimetableRepository(path=live)) as repo:
        replace_with(live=live, trip='B-0810')
        assert repo.stop_name(stop_id='215020') == 'Parramatta Station'
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


REAL = Path(__file__).parent.parent / 'fixtures' / 'real'
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
    db = build_db(
        tmp_path=tmp_path_factory.mktemp('slice'),
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
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
        sql='SELECT lon, lat FROM stops WHERE stop_id = ?',
        params=(HOLLIS,),
    )[0]
    assert found.stop_id == HOLLIS
    assert found.name == slice_repo.stop_name(stop_id=HOLLIS)
    lon, lat = (float(str(value)) for value in row)
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
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A cache past its limit is cleared before it takes more."""
    slice_repo.route_labels(trip_ids=['a1'])
    slice_repo.labels.update({f'x{i}': '1' for i in range(20_001)})
    trip = slice_departures(repo=slice_repo)[0].trip_id
    assert slice_repo.route_labels(trip_ids=[trip]) == {trip: '606'}
    assert len(slice_repo.labels) <= 2


def test_modes_near_finds_bus_at_the_606_stop(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A box around the 606 stop holds buses; a distant one holds none."""
    x, y = slice_repo.stop(stop_id=HOLLIS).point or (0.0, 0.0)
    box = (x - 2000.0, y - 2000.0, x + 2000.0, y + 2000.0)
    assert Mode.BUS in slice_repo.modes_near(box=box)
    sx, sy = to_metres(lon=150.0, lat=-34.9)
    far = (sx - 2000.0, sy - 2000.0, sx + 2000.0, sy + 2000.0)
    assert Mode.BUS not in slice_repo.modes_near(box=far)


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
