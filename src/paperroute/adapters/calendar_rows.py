"""Calendar facts from the timetable file."""

import sqlite3
from collections.abc import Iterable
from datetime import date
from typing import Final

HOLIDAYS: Final[str] = 'SELECT date FROM holidays'
HAS_HOLIDAYS: Final[str] = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holidays'"
)


def to_holidays(*, rows: Iterable[tuple[object, ...]]) -> frozenset[date]:
    """
    Turn holidays rows into dates.

    Parameters
    ----------
    rows : Iterable[tuple[object, ...]]
        Rows of HOLIDAYS.

    Returns
    -------
    frozenset[date]
        The holidays.
    """
    return frozenset(date.fromisoformat(str(row[0])) for row in rows)


def read_holidays(*, conn: sqlite3.Connection) -> frozenset[date]:
    """
    Read the public holidays the timetable records.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open timetable file.

    Returns
    -------
    frozenset[date]
        The holidays; empty when the file has no holidays table.
    """
    if conn.execute(HAS_HOLIDAYS).fetchone() is None:
        return frozenset()
    return to_holidays(rows=conn.execute(HOLIDAYS))
