"""Write a tiny timetable of stops for tile build tests."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

from paperroute.build.timetable import SCHEMA

STOPS: Final[tuple[tuple[str, float, float], ...]] = (
    ('s1', 151.000, -33.800),
    ('s2', 151.004, -33.805),
)


def write_timetable(*, path: Path) -> None:
    """
    Write a timetable holding only two stops inside ``TINY_BBOX``.

    Parameters
    ----------
    path : Path
        Destination database file.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO stops VALUES (?, ?, NULL, ?, ?, '')",
            [(stop_id, stop_id, lat, lon) for stop_id, lon, lat in STOPS],
        )
        conn.commit()
