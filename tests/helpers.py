"""Shared helpers for timetable files and stepping the reducer in tests."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute_sydney.build.dedupe import merge_rail
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.messages import Effect, Event
from paperroute_transit.core.state_model import State

REAL: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'


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
        'runs', 'run_stops', 'run_calls', 'trip_runs', 'trip_next',
        'trip_joins', 'route_patterns', 'chooser_routes', 'chooser_towards',
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

    Only the line_stations, line_aliases, line_track, line_exits and
    run_calls tables and trip_next's next_trip column are dropped, and
    the schema version set to '3'.

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
        conn.execute('DROP TABLE IF EXISTS run_calls')
        conn.execute('ALTER TABLE trip_next DROP COLUMN next_trip')
        conn.execute(
            "UPDATE meta SET value = '3' WHERE key = 'schema_version'",
        )
        conn.commit()


def schema_four(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-4 file.

    Only trips' service column is dropped, and the schema version set to
    '4'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('ALTER TABLE trips DROP COLUMN service')
        conn.execute(
            "UPDATE meta SET value = '4' WHERE key = 'schema_version'",
        )
        conn.commit()


def schema_five(*, path: Path) -> None:
    """
    Turn a built timetable back into a schema-5 file.

    run_calls is made again without its one-way columns, trip_joins is
    dropped if there, and the schema version set to '5'.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            'CREATE TABLE old_calls (run_id INTEGER NOT NULL, '
            'stops TEXT NOT NULL);'
            'INSERT INTO old_calls SELECT run_id, stops FROM run_calls;'
            'DROP TABLE run_calls;'
            'ALTER TABLE old_calls RENAME TO run_calls;'
            'CREATE INDEX run_calls_run ON run_calls (run_id);'
            'DROP TABLE IF EXISTS trip_joins;'
            "UPDATE meta SET value = '5' WHERE key = 'schema_version';",
        )
        conn.commit()


def slice_db(*, tmp_path: Path) -> Path:
    """
    Build a timetable database from the real GTFS slice, with shapes.

    Parameters
    ----------
    tmp_path : Path
        Empty directory for the database.

    Returns
    -------
    Path
        The database file.
    """
    # feeds imports this module, so it can only be imported here.
    from tests.build.feeds import build_db  # pylint: disable=C0415

    db = build_db(
        tmp_path=tmp_path,
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
    return db
