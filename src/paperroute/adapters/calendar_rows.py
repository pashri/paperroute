"""Calendar facts from the timetable file."""

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Final

type Fetch = Callable[[str], list[tuple[object, ...]]]

HOLIDAYS: Final[str] = 'SELECT date FROM holidays'
HAS_HOLIDAYS: Final[str] = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holidays'"
)


def read_holidays(*, fetch: Fetch) -> frozenset[date]:
    """
    Read the public holidays the timetable records.

    Parameters
    ----------
    fetch : Fetch
        Runs one query against the timetable file and returns its rows.

    Returns
    -------
    frozenset[date]
        The holidays; empty when the file has no holidays table, including
        when the file is replaced by one without it between the queries.
    """
    try:
        if not fetch(HAS_HOLIDAYS):
            return frozenset()
        rows = fetch(HOLIDAYS)
    except sqlite3.OperationalError:
        return frozenset()
    return frozenset(date.fromisoformat(str(row[0])) for row in rows)


def connection_fetch(*, conn: sqlite3.Connection) -> Fetch:
    """
    Make a fetch that queries an open connection.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open timetable file.

    Returns
    -------
    Fetch
        Runs a query on conn.
    """
    return lambda sql: conn.execute(sql).fetchall()
