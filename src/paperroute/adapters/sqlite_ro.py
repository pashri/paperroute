"""Read-only SQLite access that follows a file replaced on disk."""

import logging
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)
Identity = tuple[int, int]


def file_identity(*, path: Path) -> Identity | None:
    """
    Identify a file by device and inode.

    Parameters
    ----------
    path : Path
        File to look at.

    Returns
    -------
    tuple[int, int] | None
        (st_dev, st_ino), or None when the file doesn't exist.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino)


class ReadOnlyDatabase:
    """
    A read-only connection that reopens when its file is replaced.

    Parameters
    ----------
    path : Path | None
        Database file.
    connection : sqlite3.Connection | None
        An open connection to use instead of opening path. close() closes
        it too. A database given a connection never reopens.
    probe : str
        Query that must run on a replacement file before it is accepted.
    label : str
        Name used in log messages.
    allow_missing : bool
        When true, a missing file is not an error: fetch() returns no rows
        until the file appears, then opens it.

    Notes
    -----
    The file's identity is checked before each query and the connection
    is reopened if the file was replaced. A lock serialises queries, the
    reopen and close(), so a connection is never closed while another
    thread is querying it.
    """

    def __init__(
        self,
        *,
        path: Path | None,
        connection: sqlite3.Connection | None,
        probe: str,
        label: str,
        allow_missing: bool = False,
    ) -> None:
        self.lock = threading.Lock()
        self.path = None if connection else path
        self.probe = probe
        self.label = label
        self.conn: sqlite3.Connection | None = connection
        self.file_id: Identity | None = None
        self.refused: Identity | None = None
        self.closed = False
        if connection is None and path is not None:
            if file_identity(path=path) or not allow_missing:
                self.conn, self.file_id = self.open(path=path)

    @property
    def identity(self) -> Identity | None:
        """
        The identity of the file the open connection reads.

        Returns
        -------
        tuple[int, int] | None
            (st_dev, st_ino), or None when there is no file.
        """
        return self.file_id

    def open(
        self, *, path: Path
    ) -> tuple[sqlite3.Connection, Identity | None]:
        """
        Open the file read-only.

        The file is identified before it is opened, so a replacement
        landing in between shows up as a change on the next check.

        Parameters
        ----------
        path : Path
            Database file.

        Returns
        -------
        tuple[sqlite3.Connection, tuple[int, int] | None]
            New connection, usable from any thread, and the file's
            (st_dev, st_ino).
        """
        identity = file_identity(path=path)
        conn = sqlite3.connect(
            f'file:{quote(str(path))}?mode=ro',
            uri=True,
            check_same_thread=False,
        )
        return conn, identity

    def open_checked(
        self, *, path: Path
    ) -> tuple[sqlite3.Connection, Identity | None] | None:
        """
        Open the file and check the probe query runs on it.

        Parameters
        ----------
        path : Path
            Database file.

        Returns
        -------
        tuple[sqlite3.Connection, tuple[int, int] | None] | None
            The connection and file identity, or None (after logging a
            warning) when the file can't be used.
        """
        conn = None
        try:
            conn, identity = self.open(path=path)
            conn.execute(self.probe).fetchall()
        except (sqlite3.Error, OSError):
            logger.warning(
                '%s replacement unusable',
                self.label,
                exc_info=True,
            )
            if conn is not None:
                conn.close()
            return None
        return conn, identity

    def reopen_if_replaced(self) -> None:
        """
        Swap in a new connection when the file has been replaced.

        Does nothing for an injected connection or a missing file. A
        replacement that can't be read is refused and the old connection
        kept. A refused file is not tried or logged again until it is
        replaced by another. The caller must hold the lock.
        """
        if self.path is None:
            return
        try:
            current = file_identity(path=self.path)
        except OSError:
            logger.warning('%s stat failed', self.label, exc_info=True)
            return
        if current is None or current == self.refused:
            return
        if current == self.file_id and self.conn is not None:
            return
        opened = self.open_checked(path=self.path)
        if opened is None:
            self.refused = current
            return
        old = self.conn
        self.conn, self.file_id = opened
        if old is not None:
            with closing(old):
                logger.info('%s reopened', self.label)

    def fetch(
        self, *, sql: str, params: tuple[object, ...] | dict[str, object]
    ) -> list[tuple[object, ...]]:
        """
        Run a query under the lock against the current file.

        Parameters
        ----------
        sql : str
            Query.
        params : tuple[object, ...] | dict[str, object]
            Query parameters.

        Returns
        -------
        list[tuple[object, ...]]
            All rows; none while a missing file is still missing.

        Raises
        ------
        sqlite3.ProgrammingError
            When the database has been closed.
        """
        with self.lock:
            if self.closed:
                raise sqlite3.ProgrammingError('database is closed')
            self.reopen_if_replaced()
            if self.conn is None:
                return []
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Close the database connection."""
        with self.lock:
            self.closed = True
            if self.conn is not None:
                self.conn.close()
                self.conn = None
