"""Tests for merging duplicate rail trips."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from paperroute.build.dedupe import merge_rail
from paperroute.build.timetable import Source
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

START = date(2026, 10, 16)
TRAINS_ROUTE = [
    {'route_id': 'sT1', 'route_short_name': 'T1', 'route_type': '2'}
]


def merged(*, tmp_path: Path) -> tuple[Path, int]:
    """
    Build Sydney Trains then bundle rail and bus, then merge.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Returns
    -------
    tuple[Path, int]
        Database file and trips dropped.
    """
    sources = [
        Source(
            name='sydneytrains',
            archive=sydneytrains_feed(path=tmp_path / 's.zip'),
        ),
        Source(
            name='bundle',
            archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
        ),
        Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
    ]
    db = build_db(tmp_path=tmp_path, start=START, days=2, sources=sources)
    with closing(sqlite3.connect(db)) as conn:
        return db, merge_rail(conn=conn).dropped


def trip_ids(*, db: Path) -> set[str]:
    """
    All trip IDs in a database.

    Parameters
    ----------
    db : Path
        Database file.

    Returns
    -------
    set[str]
        Trip IDs.
    """
    with closing(sqlite3.connect(db)) as conn:
        return {row[0] for row in conn.execute('SELECT trip_id FROM trips')}


def one_day(*, service: str, day: str) -> dict[str, str]:
    """
    A calendar.txt row for a service running on one date.

    Parameters
    ----------
    service : str
        Service ID.
    day : str
        Date as YYYYMMDD.

    Returns
    -------
    dict[str, str]
        Calendar row.
    """
    return {
        **DAILY,
        'service_id': service,
        'start_date': day,
        'end_date': day,
    }


def rail_db(*, tmp_path: Path, trains: Path) -> Path:
    """
    Build a database from a Sydney Trains archive and the bundle rail feed.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    trains : Path
        Sydney Trains archive.

    Returns
    -------
    Path
        Database file.
    """
    sources = [
        Source(name='sydneytrains', archive=trains),
        Source(
            name='bundle',
            archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
        ),
    ]
    return build_db(tmp_path=tmp_path, start=START, days=2, sources=sources)


def test_merge_drops_duplicate_and_aliases_it(tmp_path: Path) -> None:
    """The bundle's copy of a Sydney Trains run is dropped and aliased."""
    db, dropped = merged(tmp_path=tmp_path)
    assert dropped == 1
    assert 'B-0835' not in trip_ids(db=db)
    with closing(sqlite3.connect(db)) as conn:
        alias = conn.execute(
            "SELECT trip_id FROM trip_aliases WHERE alias_id = 'B-0835'"
        )
        assert alias.fetchall() == [('S-0835',)]
        times = conn.execute(
            "SELECT COUNT(*) FROM stop_times WHERE trip_id = 'B-0835'"
        )
        assert times.fetchone() == (0,)


def test_merge_commits_its_changes(tmp_path: Path) -> None:
    """A second connection sees the merge while the first is still open."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
        with closing(sqlite3.connect(db)) as other:
            alias = other.execute(
                "SELECT trip_id FROM trip_aliases WHERE alias_id = 'B-0835'"
            )
            assert alias.fetchall() == [('S-0835',)]
            gone = other.execute(
                "SELECT COUNT(*) FROM trips WHERE trip_id = 'B-0835'"
            )
            assert gone.fetchone() == (0,)


def test_merge_rolls_back_when_a_step_fails(tmp_path: Path) -> None:
    """A failure midway leaves the timetable as it was."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            'CREATE TRIGGER stop_delete BEFORE DELETE ON trips '
            "BEGIN SELECT RAISE(ABORT, 'no'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            merge_rail(conn=conn)
        times = conn.execute(
            "SELECT COUNT(*) FROM stop_times WHERE trip_id = 'B-0835'"
        )
        assert times.fetchone() != (0,)
        assert conn.execute('SELECT * FROM trip_aliases').fetchall() == []


def test_merge_keeps_unmatched_rail_and_buses(tmp_path: Path) -> None:
    """Bundle rail with no duplicate, and non-rail trips, stay."""
    db, _ = merged(tmp_path=tmp_path)
    kept = {'B-0900', 'B-NB', 'bus-0803', 'S-0835', 'S-0848'}
    assert kept <= trip_ids(db=db)


def test_merge_keeps_partial_duplicates(tmp_path: Path) -> None:
    """A bundle trip covered on only some of its dates is kept."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "DELETE FROM services WHERE service_id = 'sydneytrains:s-daily' "
            "AND date = '2026-10-17'"
        )
        report = merge_rail(conn=conn)
    assert report.dropped == 0
    assert report.partial == 1
    assert 'B-0835' in trip_ids(db=db)


def test_merge_ignores_pass_through_stops(tmp_path: Path) -> None:
    """Rows where the train passes without stopping don't change the key."""
    passing = {
        'trip_id': 'S-0835',
        'stop_sequence': '1',
        'stop_id': '2000321',
        'arrival_time': '08:20:00',
        'departure_time': '08:20:00',
        'pickup_type': '1',
        'drop_off_type': '1',
    }
    calls = [
        {**row, 'stop_sequence': str(index + 2)}
        for index, row in enumerate(
            stop_times(
                trip='S-0835',
                times=[('2150411', '08:35:00'), ('2000321', '09:02:00')],
            )
        )
    ]
    trains = write_feed(
        path=tmp_path / 's.zip',
        files={
            'stops.txt': STATIONS,
            'routes.txt': TRAINS_ROUTE,
            'calendar.txt': [{'service_id': 's-daily', **DAILY}],
            'trips.txt': [
                {
                    'route_id': 'sT1',
                    'service_id': 's-daily',
                    'trip_id': 'S-0835',
                }
            ],
            'stop_times.txt': [passing, *calls],
        },
    )
    db = rail_db(tmp_path=tmp_path, trains=trains)
    with closing(sqlite3.connect(db)) as conn:
        assert merge_rail(conn=conn).dropped == 1
    assert 'B-0835' not in trip_ids(db=db)


def test_merge_aliases_every_kept_copy(tmp_path: Path) -> None:
    """A bundle trip covered by a different feed trip each day aliases both."""
    trains = write_feed(
        path=tmp_path / 's.zip',
        files={
            'stops.txt': STATIONS,
            'routes.txt': TRAINS_ROUTE,
            'calendar.txt': [
                one_day(service='d16', day='20261016'),
                one_day(service='d17', day='20261017'),
            ],
            'trips.txt': [
                {
                    'route_id': 'sT1',
                    'service_id': f'd{n}',
                    'trip_id': f'S-{n}',
                }
                for n in (16, 17)
            ],
            'stop_times.txt': [
                row
                for n in (16, 17)
                for row in stop_times(
                    trip=f'S-{n}',
                    times=[('2150411', '08:35:00'), ('2000321', '09:02:00')],
                )
            ],
        },
    )
    db = rail_db(tmp_path=tmp_path, trains=trains)
    with closing(sqlite3.connect(db)) as conn:
        assert merge_rail(conn=conn).dropped == 1
        aliases = conn.execute(
            'SELECT trip_id FROM trip_aliases '
            "WHERE alias_id = 'B-0835' ORDER BY trip_id"
        )
        assert aliases.fetchall() == [('S-16',), ('S-17',)]


def test_merge_drops_regional_copy_under_another_line_name(
    tmp_path: Path,
) -> None:
    """An XPT a second apart under another line name is the same train."""
    db = build_db(
        tmp_path=tmp_path,
        start=START,
        days=2,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with closing(sqlite3.connect(db)) as conn:
        assert merge_rail(conn=conn).dropped == 1
        aliases = conn.execute('SELECT alias_id, trip_id FROM trip_aliases')
        assert aliases.fetchall() == [('NT31-bundle', 'NT31-feed')]
        agency = conn.execute(
            "SELECT agency_id FROM trips WHERE trip_id = 'NT31-feed'"
        )
        assert agency.fetchall() == [('711',)]
    assert trip_ids(db=db) == {'NT31-feed'}
