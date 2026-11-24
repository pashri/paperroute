"""Shared helpers for the timetable db tests."""

import os
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.build.dedupe import merge_rail
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyMode
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.factories import pin_of, place
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.model import Departure, Pin
from tests.build.feeds import (
    DAILY,
    STATIONS,
    build_db,
    bus_feed,
    regional_sources,
    shaped_bus_feed,
    stop_times,
)

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'


MONDAY: Final[date] = date(2026, 10, 19)


HOLLIS_TO_PARRA = pin_of('2145615', '215020')


PARRA_TO_CENTRAL = pin_of('215020', '200060')


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
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY_ZONE)


WINDOW: Final[dict[str, str]] = {
    'window_start': '2026-10-15', 'window_days': '14',
}


def bundle_db(*, tmp_path: Path, days: int) -> Path:
    """
    Build a timetable of the bus feed from 15 October.

    Parameters
    ----------
    tmp_path : Path
        Directory for the file.
    days : int
        Days of service to build.

    Returns
    -------
    Path
        The timetable file.
    """
    return build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=days,
        sources=[
            Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
        ],
    )


def opened(*, path: Path) -> closing[SqliteTimetableRepository]:
    """
    Open a timetable repository for Sydney, to close on leaving.

    Parameters
    ----------
    path : Path
        The timetable file.

    Returns
    -------
    closing[SqliteTimetableRepository]
        A context manager that gives the repository and closes it.
    """
    return closing(SqliteTimetableRepository(path=path, city=SYDNEY))


def meta_db(*, tmp_path: Path, rows: dict[str, str]) -> Path:
    """
    Build a small timetable whose meta holds only the given rows.

    Parameters
    ----------
    tmp_path : Path
        Directory for the file.
    rows : dict[str, str]
        Meta keys and values.

    Returns
    -------
    Path
        The timetable file.
    """
    db = bundle_db(tmp_path=tmp_path, days=3)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('DELETE FROM meta')
        conn.executemany('INSERT INTO meta VALUES (?, ?)', rows.items())
        conn.commit()
    return db


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


HOLLIS = '2145615'


AROUND = date(2026, 10, 16)


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
            kind=SydneyMode.TRAIN,
            routes=('T2',),
        ),
        start=at(hour=hours[0], minute=0, day=6),
        end=at(hour=hours[1], minute=59, day=6),
    )


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


def platform_db(
    *,
    tmp_path: Path,
    trips: list[tuple[str, str, list[tuple[str, str]]]],
    extra_stops: Sequence[dict[str, str]] = (),
) -> Path:
    """
    Build a timetable with station P (platforms P_1, P_2) and stop Q.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    trips : list[tuple[str, str, list[tuple[str, str]]]]
        Each trip's ID, headsign and calls.
    extra_stops : Sequence[dict[str, str]]
        Further ``stops.txt`` rows.

    Returns
    -------
    Path
        The built timetable file.
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
        *extra_stops,
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
    return build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(name='sydneytrains', archive=archive)],
    )


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
    return SqliteTimetableRepository(
        path=platform_db(tmp_path=tmp_path, trips=trips),
        city=SYDNEY,
    )


def bus_bundle_db(
    *, tmp_path: Path, folder: str | None = None, shaped: bool = False,
) -> Path:
    """
    Build a one-source timetable holding the bundle bus feed.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    folder : str | None
        Sub-folder of ``tmp_path`` to build in.
    shaped : bool
        Carry a shape for the bus trip.

    Returns
    -------
    Path
        The built timetable file.
    """
    archive = (
        shaped_bus_feed(
            path=tmp_path / 's.zip',
            points=[(150.98, -33.78), (151.0, -33.82)],
        )
        if shaped
        else bus_feed(path=tmp_path / 'b.zip')
    )
    return build_db(
        tmp_path=make_dir(path=tmp_path / folder) if folder else tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[Source(name='bundle', archive=archive)],
    )


def regional_db(*, tmp_path: Path, days: int, trim: bool = False) -> Path:
    """
    Build a regional timetable and merge its duplicate rail services.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    days : int
        Days the timetable covers from 16 October 2026.
    trim : bool
        Drop the daily XPT service from 17 October first.

    Returns
    -------
    Path
        The built timetable file.
    """
    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 16),
        days=days,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with closing(sqlite3.connect(db)) as conn:
        if trim:
            conn.execute(
                "DELETE FROM services "
                "WHERE service_id = 'sydneytrains:s-daily' "
                "AND date = '2026-10-17'"
            )
            conn.commit()
        merge_rail(conn=conn)
    return db


def hollis_departures(
    *, repo: SqliteTimetableRepository,
) -> list[Departure]:
    """
    Read the 8am hour of departures from Hollis to Parramatta.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to read.

    Returns
    -------
    list[Departure]
        The departures found.
    """
    return repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=0),
    )


def three_day_db(*, tmp_path: Path, sources: list[Source]) -> Path:
    """
    Build a timetable covering three days from 15 October 2026.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    sources : list[Source]
        Feeds to build from.

    Returns
    -------
    Path
        The built timetable file.
    """
    return build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=sources,
    )


def c_departures(
    *, repo: SqliteTimetableRepository,
) -> tuple[list[Departure], list[Departure]]:
    """
    Read the 7am departures from C, to W2 and to anywhere.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to read.

    Returns
    -------
    tuple[list[Departure], list[Departure]]
        Departures to W2, then departures to anywhere.
    """
    to_w2 = repo.departures(
        pin=pin_of('C', 'W2'),
        start=at(hour=7, minute=0, day=6),
        end=at(hour=7, minute=10, day=6),
    )
    return to_w2, whole_stop(repo=repo, origin='C', hours=(7, 7))


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
