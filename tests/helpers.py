"""Shared helpers for writing real GTFS zip files in tests."""

import csv
import io
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path


def to_csv(rows: list[dict[str, str]]) -> str:
    """
    Render rows as CSV with a header of every key used.

    Parameters
    ----------
    rows : list[dict[str, str]]
        Records.

    Returns
    -------
    str
        CSV text.
    """
    fields = list(dict.fromkeys(key for row in rows for key in row))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_feed(*, path: Path, files: dict[str, list[dict[str, str]]]) -> Path:
    """
    Write a GTFS zip with one CSV member per file name.

    Parameters
    ----------
    path : Path
        Zip file to create.
    files : dict[str, list[dict[str, str]]]
        Member name (for example 'stops.txt') to its rows.

    Returns
    -------
    Path
        The zip path.
    """
    with zipfile.ZipFile(path, 'w') as archive:
        for name, rows in files.items():
            archive.writestr(name, to_csv(rows))
    return path


def old_schema(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-2 file.

    The run, carry-on and chooser tables are dropped and the schema version
    set to '2'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    tables = (
        'runs', 'run_stops', 'trip_runs', 'trip_next', 'route_patterns',
        'chooser_routes', 'chooser_towards',
    )
    with closing(sqlite3.connect(path)) as conn:
        for table in tables:
            conn.execute(f'DROP TABLE IF EXISTS {table}')
        conn.execute(
            "UPDATE meta SET value = '2' WHERE key = 'schema_version'",
        )
        conn.commit()


def schema_three(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-3 file.

    Only the line_stations, line_aliases, line_track and line_exits
    tables and trip_next's next_trip column are dropped, and the schema
    version set to '3'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE IF EXISTS line_stations')
        conn.execute('DROP TABLE IF EXISTS line_aliases')
        conn.execute('DROP TABLE IF EXISTS line_exits')
        conn.execute('DROP TABLE IF EXISTS line_track')
        conn.execute('ALTER TABLE trip_next DROP COLUMN next_trip')
        conn.execute(
            "UPDATE meta SET value = '3' WHERE key = 'schema_version'",
        )
        conn.commit()
