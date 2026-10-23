"""Tests for reading public holidays from a timetable file."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute.adapters.calendar_rows import (
    HAS_HOLIDAYS,
    connection_fetch,
    read_holidays,
)
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from tests.build.feeds import bus_files
from tests.helpers import write_feed

HOLIDAY: Final[date] = date(2026, 10, 16)
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
    repo = SqliteTimetableRepository(path=db)
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
    repo = SqliteTimetableRepository(path=db)
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
