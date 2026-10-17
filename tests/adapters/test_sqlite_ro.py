"""Tests for the read-only database wrapper."""

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from paperroute.adapters.sqlite_ro import ReadOnlyDatabase, file_identity

PROBE = 'SELECT 1 FROM t LIMIT 1'


def make(*, path: Path, value: int) -> None:
    """
    Write a one-row database.

    Parameters
    ----------
    path : Path
        Destination file.
    value : int
        The row's value.
    """
    staged = path.with_name('staged.db')
    with closing(sqlite3.connect(staged)) as conn:
        conn.execute('CREATE TABLE t (v INTEGER)')
        conn.execute('INSERT INTO t VALUES (?)', (value,))
        conn.commit()
    staged.replace(path)


def test_file_identity_missing_is_none(tmp_path: Path) -> None:
    """A missing file has no identity."""
    assert file_identity(path=tmp_path / 'nope') is None


def test_fetch_missing_allowed_then_opens(tmp_path: Path) -> None:
    """A missing file answers no rows until it appears."""
    path = tmp_path / 'a.db'
    db = ReadOnlyDatabase(
        path=path,
        connection=None,
        probe=PROBE,
        label='test',
        allow_missing=True,
    )
    with closing(db):
        assert db.identity is None
        assert db.fetch(sql='SELECT v FROM t', params=()) == []
        make(path=path, value=3)
        assert db.fetch(sql='SELECT v FROM t', params=()) == [(3,)]
        assert db.identity == file_identity(path=path)


def open_db(*, path: Path) -> ReadOnlyDatabase:
    """
    Open a database on a file.

    Parameters
    ----------
    path : Path
        Database file.

    Returns
    -------
    ReadOnlyDatabase
        The database.
    """
    return ReadOnlyDatabase(
        path=path, connection=None, probe=PROBE, label='test'
    )


def test_connection_is_read_only(tmp_path: Path) -> None:
    """A write through fetch is refused."""
    path = tmp_path / 'a.db'
    make(path=path, value=1)
    with closing(open_db(path=path)) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.fetch(sql='INSERT INTO t VALUES (2)', params=())


@pytest.mark.parametrize('name', ['a?b.db', 'a#b.db', 'a b%20.db'])
def test_path_with_uri_characters_opens(tmp_path: Path, name: str) -> None:
    """Characters that mean something in a URI are quoted."""
    path = tmp_path / name
    make(path=path, value=7)
    with closing(open_db(path=path)) as db:
        assert db.fetch(sql='SELECT v FROM t', params=()) == [(7,)]


def test_refused_file_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unusable file is logged once, not on every fetch."""
    path = tmp_path / 'a.db'
    db = ReadOnlyDatabase(
        path=path,
        connection=None,
        probe=PROBE,
        label='test',
        allow_missing=True,
    )
    with closing(db):
        junk = tmp_path / 'junk'
        junk.write_bytes(b'not a database' * 100)
        os.replace(junk, path)
        with caplog.at_level(logging.INFO):
            for _ in range(3):
                assert db.fetch(sql='SELECT v FROM t', params=()) == []
            make(path=path, value=5)
            assert db.fetch(sql='SELECT v FROM t', params=()) == [(5,)]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_fetch_after_close_raises(tmp_path: Path) -> None:
    """A closed database refuses queries instead of reopening."""
    path = tmp_path / 'a.db'
    make(path=path, value=1)
    db = open_db(path=path)
    db.close()
    db.close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.fetch(sql='SELECT v FROM t', params=())
