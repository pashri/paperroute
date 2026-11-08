"""Tests for reading public holidays from a timetable file."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.timetable import Window, build_timetable
from paperroute.city import SYDNEY_PROJECTION, SYDNEY_ZONE
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.factories import pin_of
from paperroute_transit.adapters.calendar_rows import (
    HAS_HOLIDAYS,
    connection_fetch,
    read_holidays,
)
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.model import (
    ServiceSpan,
    service_epoch,
    service_time,
)
from tests.build.feeds import build_db, bus_files

HOLIDAY: Final[date] = date(2026, 10, 16)
REAL: Final[Path] = Path(__file__).parent.parent / 'fixtures' / 'real'
SERVICE: Final[date] = date(2026, 10, 16)
WEEKDAYS: Final[dict[str, str]] = {
    'monday': '1', 'tuesday': '1', 'wednesday': '1', 'thursday': '1',
    'friday': '1', 'saturday': '0', 'sunday': '0',
    'start_date': '20261001', 'end_date': '20261031'}


def holiday_db(*, tmp_path: Path) -> Path:
    """
    Build a timetable whose bundle removes every weekday service on a day.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip and the file.

    Returns
    -------
    Path
        The timetable file.
    """
    files = bus_files()
    ids = [f'w{number}' for number in range(10)]
    files['calendar.txt'] = [
        *files['calendar.txt'],
        *({'service_id': service, **WEEKDAYS} for service in ids),
    ]
    files['calendar_dates.txt'] = [
        {'service_id': service, 'date': '20261016', 'exception_type': '2'}
        for service in ids
    ]
    archive = write_feed(path=tmp_path / 'bundle.zip', files=files)
    out = tmp_path / 'timetable.db'
    build_timetable(
        sources=[Source(name='bundle', archive=archive)],
        window=Window(start=HOLIDAY, days=3),
        out=out,
    )
    return out


def test_holidays_round_trip(tmp_path: Path) -> None:
    """A built file's holidays read back, directly and from the repo."""
    db = holiday_db(tmp_path=tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        assert read_holidays(fetch=connection_fetch(conn=conn)) == {HOLIDAY}
    repo = SqliteTimetableRepository(
        path=db,
        zone=SYDNEY_ZONE,
        projection=SYDNEY_PROJECTION,
    )
    try:
        assert repo.holidays() == {HOLIDAY}
    finally:
        repo.close()


def test_holidays_absent_table(tmp_path: Path) -> None:
    """A file from before the holidays table has no holidays."""
    db = holiday_db(tmp_path=tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('DROP TABLE holidays')
        conn.commit()
        assert read_holidays(fetch=connection_fetch(conn=conn)) == frozenset()
    repo = SqliteTimetableRepository(
        path=db,
        zone=SYDNEY_ZONE,
        projection=SYDNEY_PROJECTION,
    )
    try:
        assert repo.holidays() == frozenset()
    finally:
        repo.close()


def test_holidays_query_error_is_none() -> None:
    """A table that vanishes between the check and the read gives none."""

    def vanishing(sql: str) -> list[tuple[object, ...]]:
        if sql == HAS_HOLIDAYS:
            return [(1,)]
        raise sqlite3.OperationalError('no such table: holidays')

    assert read_holidays(fetch=vanishing) == frozenset()


@pytest.fixture(name='bundle_repo', scope='module')
def _bundle_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the real bus slice, built for one service day.

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
        tmp_path=tmp_path_factory.mktemp('bundle'),
        sources=[Source(name='bundle', archive=REAL / 'bundle.zip')],
        start=SERVICE,
        days=1,
    )
    with closing(SqliteTimetableRepository(
        path=db,
        zone=SYDNEY_ZONE,
        projection=SYDNEY_PROJECTION,
    )) as repo:
        yield repo


def test_service_span_reads_trip_ends(
    bundle_repo: SqliteTimetableRepository,
) -> None:
    """A span runs from the first trip's start to the last trip's end."""
    pin = pin_of('2145615', '215020')
    trips = {
        found.trip_id
        for found in bundle_repo.departures(
            pin=pin,
            start=service_epoch(day=SERVICE, zone=SYDNEY_ZONE),
            end=service_epoch(
                day=SERVICE + timedelta(days=1),
                zone=SYDNEY_ZONE,
            ),
        )
        if found.service_day == SERVICE
    }
    assert trips
    marks = ','.join('?' * len(trips))
    ((first, last),) = bundle_repo.fetch(
        sql=(
            'SELECT MIN(dep), MAX(arr) FROM stop_times s WHERE trip_id IN '
            f'({marks}) AND (seq = (SELECT MIN(seq) FROM stop_times '
            'WHERE trip_id = s.trip_id) OR seq = (SELECT MAX(seq) FROM '
            'stop_times WHERE trip_id = s.trip_id))'
        ),
        params=tuple(sorted(trips)),
    )
    span = bundle_repo.service_span(pin=pin, day=SERVICE)
    assert span is not None
    assert span.first_start == service_time(
        day=SERVICE,
        seconds=int(str(first)),
        zone=SYDNEY_ZONE,
    )
    assert span.last_end == service_time(
        day=SERVICE,
        seconds=int(str(last)),
        zone=SYDNEY_ZONE,
    )
    after = SERVICE + timedelta(days=5)
    assert bundle_repo.service_span(pin=pin, day=after) is None


def test_service_span_counts_trips_after_midnight(tmp_path: Path) -> None:
    """A trip leaving after 24:00 on the service day ends the span."""
    archive = write_feed(path=tmp_path / 'bundle.zip', files=bus_files())
    out = tmp_path / 'timetable.db'
    build_timetable(
        sources=[Source(name='bundle', archive=archive)],
        window=Window(start=SERVICE, days=3),
        out=out,
    )
    with closing(
        SqliteTimetableRepository(
            path=out,
            zone=SYDNEY_ZONE,
            projection=SYDNEY_PROJECTION,
        ),
    ) as repo:
        span = repo.service_span(pin=pin_of('2145615', '215020'), day=SERVICE)
    assert span == ServiceSpan(
        first_start=service_time(
            day=SERVICE,
            seconds=8 * 3600 + 3 * 60,
            zone=SYDNEY_ZONE,
        ),
        last_end=service_time(
            day=SERVICE,
            seconds=25 * 3600 + 40 * 60,
            zone=SYDNEY_ZONE,
        ),
    )
