"""Stream GTFS sources into a SQLite timetable."""

import os
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import batched
from pathlib import Path
from typing import Final

from paperroute.build.dedupe import MergeReport, merge_rail
from paperroute.build.gtfs_files import (
    Source,
    active_dates,
    parse_time,
    read_rows,
)
from paperroute.build.shapes import load_shapes
from paperroute.core.model import SYDNEY, Mode, mode_for

BATCH: Final[int] = 10_000
SCHEMA_VERSION: Final[str] = '2'
PLATFORM: Final[re.Pattern[str]] = re.compile(r'(?:Platform|Stand)\s+(\w+)$')
SCHEMA: Final[str] = """
CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT NOT NULL,
  parent_station TEXT, lat REAL, lon REAL, platform TEXT NOT NULL);
CREATE TABLE routes (route_id TEXT PRIMARY KEY, short_name TEXT NOT NULL,
  long_name TEXT NOT NULL, type INTEGER NOT NULL, color TEXT, text_color TEXT,
  agency_id TEXT, source TEXT NOT NULL);
CREATE TABLE services (service_id TEXT NOT NULL, date TEXT NOT NULL,
  PRIMARY KEY (service_id, date)) WITHOUT ROWID;
CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT NOT NULL,
  service_id TEXT NOT NULL, headsign TEXT NOT NULL, direction_id INTEGER,
  block_id TEXT, agency_id TEXT, source TEXT NOT NULL, shape_id TEXT);
CREATE TABLE stop_times (trip_id TEXT NOT NULL, seq INTEGER NOT NULL,
  stop_id TEXT NOT NULL, arr INTEGER NOT NULL, dep INTEGER NOT NULL,
  pickup INTEGER NOT NULL, dropoff INTEGER NOT NULL,
  PRIMARY KEY (trip_id, seq)) WITHOUT ROWID;
CREATE TABLE trip_aliases (alias_id TEXT NOT NULL, trip_id TEXT NOT NULL,
  PRIMARY KEY (alias_id, trip_id)) WITHOUT ROWID;
CREATE TABLE trip_skip (trip_id TEXT NOT NULL, date TEXT NOT NULL,
  PRIMARY KEY (trip_id, date)) WITHOUT ROWID;
CREATE TABLE shapes (shape_id TEXT PRIMARY KEY, mode TEXT NOT NULL,
  west REAL NOT NULL, south REAL NOT NULL, east REAL NOT NULL,
  north REAL NOT NULL, points BLOB NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
SHAPE_MODES: Final[str] = """
SELECT DISTINCT t.shape_id, r.type, COALESCE(t.agency_id, r.agency_id)
FROM trips t JOIN routes r ON r.route_id = t.route_id
WHERE t.source = :name AND t.shape_id IS NOT NULL
ORDER BY t.shape_id, r.type, COALESCE(t.agency_id, r.agency_id)
"""
DROP_UNUSED_SHAPES: Final[str] = """
DELETE FROM shapes WHERE shape_id NOT IN
  (SELECT shape_id FROM trips WHERE shape_id IS NOT NULL)
"""
INDEXES: Final[str] = """
CREATE INDEX stop_times_stop ON stop_times (stop_id, dep);
CREATE INDEX stops_parent ON stops (parent_station);
CREATE INDEX aliases_trip ON trip_aliases (trip_id);
"""


@dataclass(frozen=True)
class Window:
    """The run of dates a timetable covers."""

    start: date
    days: int


def create_schema(*, conn: sqlite3.Connection) -> None:
    """
    Create the timetable tables.

    Parameters
    ----------
    conn : sqlite3.Connection
        Empty database.
    """
    conn.executescript(SCHEMA)


def create_indexes(*, conn: sqlite3.Connection) -> None:
    """
    Create lookup indexes after loading.

    Parameters
    ----------
    conn : sqlite3.Connection
        Loaded database.
    """
    conn.executescript(INDEXES)


def platform_of(*, row: dict[str, str]) -> str:
    """
    Find a stop's platform or stand code.

    Parameters
    ----------
    row : dict[str, str]
        A stops.txt record.

    Returns
    -------
    str
        The platform_code, else the code at the end of the name, else ''.
    """
    if row.get('platform_code'):
        return row['platform_code']
    found = PLATFORM.search(row.get('stop_name', ''))
    return found.group(1) if found else ''


def insert_many(*, conn: sqlite3.Connection, sql: str,
                rows: Iterable[tuple[object, ...]]) -> None:
    """
    Insert rows in fixed-size batches.

    Parameters
    ----------
    conn : sqlite3.Connection
        Target database.
    sql : str
        INSERT statement.
    rows : Iterable[tuple[object, ...]]
        Values.
    """
    for chunk in batched(rows, BATCH):
        conn.executemany(sql, chunk)


def stop_rows(*, archive: Path) -> Iterator[tuple[object, ...]]:
    """
    Turn stops into table rows.

    Parameters
    ----------
    archive : Path
        GTFS zip.

    Yields
    ------
    tuple[object, ...]
        stops row values.
    """
    for row in read_rows(archive=archive, member='stops.txt'):
        yield (row['stop_id'], row.get('stop_name', ''),
               row.get('parent_station') or None,
               float(row['stop_lat']) if row.get('stop_lat') else None,
               float(row['stop_lon']) if row.get('stop_lon') else None,
               platform_of(row=row))


def route_rows(*, source: Source) -> Iterator[tuple[object, ...]]:
    """
    Turn routes into namespaced table rows.

    Parameters
    ----------
    source : Source
        Feed.

    Yields
    ------
    tuple[object, ...]
        routes row values.
    """
    for row in read_rows(archive=source.archive, member='routes.txt'):
        yield (f'{source.name}:{row["route_id"]}',
               row.get('route_short_name', ''),
               row.get('route_long_name', ''), int(row['route_type']),
               row.get('route_color') or None,
               row.get('route_text_color') or None,
               row.get('agency_id') or None, source.name)


def load_services(*, conn: sqlite3.Connection, source: Source,
                  window: Window) -> set[str]:
    """
    Store each service's active dates and return the active raw IDs.

    Parameters
    ----------
    conn : sqlite3.Connection
        Target database.
    source : Source
        Feed.
    window : Window
        Dates of interest.

    Returns
    -------
    set[str]
        Raw service IDs active at least once in the window.
    """
    dates = active_dates(archive=source.archive, start=window.start,
                         days=window.days)
    insert_many(conn=conn, sql='INSERT INTO services VALUES (?, ?)',
                rows=((f'{source.name}:{sid}', day.isoformat())
                      for sid, days in dates.items()
                      for day in sorted(days)))
    return set(dates)


def load_trips(*, conn: sqlite3.Connection, source: Source,
               services: set[str]) -> set[str]:
    """
    Insert active trips not already present and return those inserted.

    Parameters
    ----------
    conn : sqlite3.Connection
        Target database.
    source : Source
        Feed.
    services : set[str]
        Raw service IDs active in the window.

    Returns
    -------
    set[str]
        Trip IDs inserted from this source.
    """
    existing = {row[0] for row in conn.execute('SELECT trip_id FROM trips')}
    rows = [(row['trip_id'], f'{source.name}:{row["route_id"]}',
             f'{source.name}:{row["service_id"]}',
             row.get('trip_headsign', ''),
             int(row['direction_id']) if row.get('direction_id') else None,
             row.get('block_id') or None, None, source.name,
             f'{source.name}:{row["shape_id"]}' if row.get('shape_id')
             else None)
            for row in read_rows(archive=source.archive, member='trips.txt')
            if row['service_id'] in services
            and row['trip_id'] not in existing]
    insert_many(conn=conn,
                sql='INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                rows=rows)
    return {row[0] for row in rows}


def shape_modes(*, conn: sqlite3.Connection,
                source: Source) -> dict[str, Mode]:
    """
    Map the raw shape IDs of one feed's stored trips to their modes.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database holding the feed's trips and routes.
    source : Source
        Feed.

    Returns
    -------
    dict[str, Mode]
        Raw shape ID to mode. A shape used by trips of several modes takes
        the one with the lowest route type, then the lowest agency.
    """
    prefix = len(f'{source.name}:')
    modes: dict[str, Mode] = {}
    for shape_id, route_type, agency_id in conn.execute(
            SHAPE_MODES, {'name': source.name}):
        modes.setdefault(shape_id[prefix:], mode_for(
            route_type=route_type, agency_id=agency_id))
    return modes


def stop_time_rows(*, archive: Path,
                   trips: set[str]) -> Iterator[tuple[object, ...]]:
    """
    Stop times of the given trips as table rows.

    Parameters
    ----------
    archive : Path
        GTFS zip.
    trips : set[str]
        Trips to keep.

    Yields
    ------
    tuple[object, ...]
        stop_times row values.
    """
    for row in read_rows(archive=archive, member='stop_times.txt'):
        if row['trip_id'] in trips:
            yield (row['trip_id'], int(row['stop_sequence']), row['stop_id'],
                   parse_time(row['arrival_time'] or row['departure_time']),
                   parse_time(row['departure_time'] or row['arrival_time']),
                   int(row.get('pickup_type') or 0),
                   int(row.get('drop_off_type') or 0))


def load_source(*, conn: sqlite3.Connection, source: Source,
                window: Window) -> int:
    """
    Load one feed's stops, routes, services, trips, stop times and shapes.

    Stops already present (from an earlier source) are kept as they are.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with the schema.
    source : Source
        Feed.
    window : Window
        Dates of interest.

    Returns
    -------
    int
        Number of trips inserted.
    """
    insert_many(conn=conn,
                sql='INSERT OR IGNORE INTO stops VALUES (?, ?, ?, ?, ?, ?)',
                rows=stop_rows(archive=source.archive))
    insert_many(
        conn=conn,
        sql='INSERT OR IGNORE INTO routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        rows=route_rows(source=source))
    services = load_services(conn=conn, source=source, window=window)
    trips = load_trips(conn=conn, source=source, services=services)
    insert_many(conn=conn,
                sql='INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, ?, ?)',
                rows=stop_time_rows(archive=source.archive, trips=trips))
    load_shapes(conn=conn, source=source,
                modes=shape_modes(conn=conn, source=source))
    conn.commit()
    return len(trips)


def write_meta(*, conn: sqlite3.Connection, window: Window) -> None:
    """
    Record the window, schema version and build time.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable being built.
    window : Window
        Dates covered.
    """
    conn.executemany('INSERT INTO meta VALUES (?, ?)', [
        ('window_start', window.start.isoformat()),
        ('window_days', str(window.days)),
        ('schema_version', SCHEMA_VERSION),
        ('built_at', datetime.now(timezone.utc).isoformat())])


def build_timetable(*, sources: list[Source], window: Window,
                    out: Path) -> MergeReport:
    """
    Build a timetable file from sources, replacing out only on success.

    Load preferred feeds (Sydney Trains, Metro) before the bundle so that a
    trip ID in both keeps the preferred copy.

    Parameters
    ----------
    sources : list[Source]
        Feeds in load order.
    window : Window
        Dates to cover.
    out : Path
        Timetable file to write.

    Returns
    -------
    MergeReport
        Result of merging rail duplicates.
    """
    scratch = out.with_suffix('.new')
    scratch.unlink(missing_ok=True)
    with closing(sqlite3.connect(scratch)) as conn:
        create_schema(conn=conn)
        for source in sources:
            print(f'progress load {source.name}', flush=True)
            load_source(conn=conn, source=source, window=window)
        print('progress merge', flush=True)
        report = merge_rail(conn=conn)
        conn.execute(DROP_UNUSED_SHAPES)
        create_indexes(conn=conn)
        write_meta(conn=conn, window=window)
        conn.commit()
    os.replace(scratch, out)
    return report


def yesterday() -> date:
    """
    The Sydney date before today.

    Starting a day early keeps the previous service day's 24:00-28:59
    trips when the build runs in the small hours.

    Returns
    -------
    date
        Yesterday in Sydney.
    """
    return datetime.now(SYDNEY).date() - timedelta(days=1)
