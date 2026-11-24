"""Tests for timetable db: reopen."""

import logging
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_device.adapters.sqlite_ro import file_identity
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from tests.adapters.timetable_db.helpers import (
    PARRA_TO_CENTRAL,
    at,
    opened,
    replace_with,
    single_trip_db,
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


def test_departures_reopens_after_file_replaced(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A rebuilt timetable moved over the file is seen by the next query."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with opened(path=live) as repo:
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
    with opened(path=live) as repo:
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
    with opened(path=live) as repo:
        replace_with(live=live, trip='B-0810')
        assert repo.stop(stop_id='215020').name == 'Parramatta Station'
        assert repo.identity == file_identity(path=live)


def test_missing_file_keeps_old_connection(tmp_path: Path) -> None:
    """With the file deleted, queries still answer from the open file."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    with opened(path=live) as repo:
        live.unlink()
        assert trip_ids(repo=repo) == ['A-0810']


def test_injected_connection_never_reopens(tmp_path: Path) -> None:
    """A repository given a connection ignores the file on disk."""
    live = single_trip_db(directory=tmp_path / 'a', trip='A-0810')
    conn = sqlite3.connect(f'file:{live}?mode=ro', uri=True)
    with closing(
        SqliteTimetableRepository(
            connection=conn,
            city=SYDNEY,
        ),
    ) as repo:
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

    with opened(path=live) as repo:
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
