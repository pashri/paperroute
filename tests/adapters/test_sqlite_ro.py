"""Tests for the read-only database wrapper."""

import sqlite3
from contextlib import closing
from pathlib import Path

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
        path=path, connection=None, probe=PROBE, allow_missing=True
    )
    with closing(db):
        assert db.identity is None
        assert db.fetch(sql='SELECT v FROM t', params=()) == []
        make(path=path, value=3)
        assert db.fetch(sql='SELECT v FROM t', params=()) == [(3,)]
        assert db.identity == file_identity(path=path)
