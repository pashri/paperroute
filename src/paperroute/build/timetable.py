"""Stream GTFS sources into a SQLite timetable."""

import gc
import json
import logging
import os
import re
import sqlite3
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Final

from paperroute.build.dedupe import MergeReport, merge_rail, relabel_trainlink
from paperroute.build.extras import build_extras
from paperroute.build.gtfs_files import (
    Source,
    active_dates,
    insert_many,
    parse_time,
    read_rows,
)
from paperroute.build.handoff import Handoff, HandoffRecord, hand_over
from paperroute.build.shapes import load_shapes
from paperroute.build.trainlink import trim_trainlink
from paperroute.core.model import (
    HIDDEN_HEADSIGNS,
    SYDNEY,
    TIMETABLE_SCHEMA,
    Mode,
    mode_for,
)
from paperroute.core.tiles import NSW_BBOX
from paperroute.rss import current_rss_mb, max_rss_mb

logger = logging.getLogger(__name__)

SCHEMA_VERSION: Final[str] = TIMETABLE_SCHEMA
CATEGORIES: Final[str] = 'vehicle_categories.txt'
SHORT_SUSPECTS: Final[str] = """
SELECT j.value FROM json_each(:trips) j
WHERE (SELECT COUNT(*) FROM stop_times st
  WHERE st.trip_id = j.value AND (st.pickup <> 1 OR st.dropoff <> 1)) < 2
OR EXISTS (SELECT 1 FROM stop_times st JOIN stops s USING (stop_id)
  WHERE st.trip_id = j.value AND (st.pickup <> 1 OR st.dropoff <> 1)
  AND NOT (s.lon BETWEEN :west AND :east AND s.lat BETWEEN :south AND :north))
"""
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
  block_id TEXT, agency_id TEXT, source TEXT NOT NULL, shape_id TEXT,
  service INTEGER NOT NULL);
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


class Fate(Enum):
    """What loading does with a trip of the window."""

    KEEP = auto()
    HIDE = auto()
    CHECK = auto()


@dataclass
class TripLoad:
    """Trips one feed inserted, the suspects among them and those hidden."""

    inserted: set[str] = field(default_factory=set)
    suspects: set[str] = field(default_factory=set)
    hidden: int = 0


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


def known_categories(*, archive: Path) -> frozenset[str] | None:
    """
    Read the vehicle categories a feed defines.

    Parameters
    ----------
    archive : Path
        GTFS zip.

    Returns
    -------
    frozenset[str] | None
        Category IDs from vehicle_categories.txt; None when the feed has
        no such file.
    """
    with zipfile.ZipFile(archive) as feed:
        if CATEGORIES not in feed.namelist():
            return None
    return frozenset(
        row['vehicle_category_id']
        for row in read_rows(archive=archive, member=CATEGORIES)
    )


def undefined_category(
    *, row: dict[str, str], categories: frozenset[str] | None,
) -> bool:
    """
    Tell whether a trip names a vehicle category its feed does not define.

    Sydney Trains marks empty running, track, special and some excursion
    trains this way.

    Parameters
    ----------
    row : dict[str, str]
        A trips.txt record.
    categories : frozenset[str] | None
        Categories the feed defines; None when it defines none.

    Returns
    -------
    bool
        True when the trip names an undefined category.
    """
    category = row.get('vehicle_category_id')
    return bool(category) and categories is not None and (
        category not in categories
    )


def revenue_routes(
    *, rows: Iterable[dict[str, str]], categories: frozenset[str] | None,
) -> set[str]:
    """
    Find the routes with at least one trip of a defined vehicle category.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        Records of trips.txt.
    categories : frozenset[str] | None
        Categories the feed defines; None when it defines none.

    Returns
    -------
    set[str]
        Raw route IDs; empty, without reading rows, for no categories.
    """
    if categories is None:
        return set()
    return {
        row['route_id']
        for row in rows
        if row.get('vehicle_category_id') in categories
    }


def trip_fate(
    *, row: dict[str, str], categories: frozenset[str] | None,
    revenue: set[str],
) -> Fate:
    """
    Decide whether a trip of the window is in service, out or checked later.

    Parameters
    ----------
    row : dict[str, str]
        A trips.txt record.
    categories : frozenset[str] | None
        Categories the feed defines; None when it defines none.
    revenue : set[str]
        Routes with a trip of a defined category.

    Returns
    -------
    Fate
        KEEP for a trip of a defined or no category. For an undefined
        category: HIDE on a route with no defined-category trip or with
        a hidden headsign, else CHECK, in service only if it has two
        stopping calls and stops nowhere outside NSW.
    """
    if not undefined_category(row=row, categories=categories):
        return Fate.KEEP
    if row['route_id'] not in revenue or (
        row.get('trip_headsign', '') in HIDDEN_HEADSIGNS
    ):
        return Fate.HIDE
    return Fate.CHECK


def trip_row(
    *, row: dict[str, str], source: Source, service: bool,
) -> tuple[object, ...]:
    """
    Turn a trips.txt record into a trips table row.

    Parameters
    ----------
    row : dict[str, str]
        A trips.txt record.
    source : Source
        Feed.
    service : bool
        Whether the trip carries passengers; a hidden headsign never does.

    Returns
    -------
    tuple[object, ...]
        Values of a trips row, IDs namespaced by the feed.
    """
    return (
        row['trip_id'], f'{source.name}:{row["route_id"]}',
        f'{source.name}:{row["service_id"]}',
        row.get('trip_headsign', ''),
        int(row['direction_id']) if row.get('direction_id') else None,
        row.get('block_id') or None, None, source.name,
        f'{source.name}:{row["shape_id"]}' if row.get('shape_id') else None,
        int(
            service
            and row.get('trip_headsign', '') not in HIDDEN_HEADSIGNS
        ),
    )


def window_trips(
    *, source: Source, services: set[str], existing: set[str],
) -> Iterator[dict[str, str]]:
    """
    Stream a feed's trips.txt records active in the window.

    Parameters
    ----------
    source : Source
        Feed.
    services : set[str]
        Raw service IDs active in the window.
    existing : set[str]
        Trip IDs already loaded, which are skipped.

    Yields
    ------
    dict[str, str]
        A trips.txt record.
    """
    for row in read_rows(archive=source.archive, member='trips.txt'):
        if row['service_id'] in services and row['trip_id'] not in existing:
            yield row


def kept_trip_rows(
    *, rows: Iterable[dict[str, str]],
    fate_of: Callable[[dict[str, str]], Fate], source: Source,
    load: TripLoad,
) -> Iterator[tuple[object, ...]]:
    """
    Give the trips table row of each record, noting each trip's fate.

    Parameters
    ----------
    rows : Iterable[dict[str, str]]
        Records of trips.txt.
    fate_of : Callable[[dict[str, str]], Fate]
        Decides a record's fate.
    source : Source
        Feed.
    load : TripLoad
        Filled with the trips, the suspects and the count hidden.

    Yields
    ------
    tuple[object, ...]
        A trips row for each record.
    """
    for row in rows:
        fate = fate_of(row)
        load.hidden += fate is Fate.HIDE
        load.inserted.add(row['trip_id'])
        if fate is Fate.CHECK:
            load.suspects.add(row['trip_id'])
        yield trip_row(
            row=row, source=source, service=fate is not Fate.HIDE,
        )


def load_trips(*, conn: sqlite3.Connection, source: Source,
               services: set[str]) -> TripLoad:
    """
    Insert active trips not already present and return those inserted.

    Trips naming a vehicle category the feed does not define are out of
    service on a non-revenue route or with a hidden headsign; the rest of
    them are suspects until their stop times are loaded. trips.txt is read
    twice, first for the revenue routes, so no trip records are held.

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
    TripLoad
        Trips inserted, suspects among them and trips hidden.
    """
    existing = {row[0] for row in conn.execute('SELECT trip_id FROM trips')}
    categories = known_categories(archive=source.archive)
    revenue = revenue_routes(
        rows=window_trips(
            source=source, services=services, existing=existing,
        ),
        categories=categories,
    )
    load = TripLoad()
    insert_many(conn=conn,
                sql='INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                rows=kept_trip_rows(
                    rows=window_trips(
                        source=source, services=services, existing=existing,
                    ),
                    fate_of=lambda row: trip_fate(
                        row=row, categories=categories, revenue=revenue,
                    ),
                    source=source, load=load,
                ))
    return load


def mark_short_suspects(
    *, conn: sqlite3.Connection, suspects: set[str],
) -> int:
    """
    Mark out of service suspects with under two stopping calls or out of NSW.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with the suspects' stop times loaded.
    suspects : set[str]
        Trip IDs naming an undefined vehicle category.

    Returns
    -------
    int
        Trips marked: those with fewer than two stopping calls, or with
        a stopping call outside NSW_BBOX.
    """
    west, south, east, north = NSW_BBOX
    short = [
        trip_id
        for (trip_id,) in conn.execute(SHORT_SUSPECTS, {
            'trips': json.dumps(sorted(suspects)), 'west': west,
            'south': south, 'east': east, 'north': north,
        })
    ]
    conn.execute(
        'UPDATE trips SET service = 0 WHERE trip_id IN '
        '(SELECT value FROM json_each(?))',
        (json.dumps(short),),
    )
    return len(short)


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
                rows=stop_time_rows(archive=source.archive,
                                    trips=trips.inserted))
    short = mark_short_suspects(conn=conn, suspects=trips.suspects)
    if trips.hidden + short:
        logger.info('kept %d %s trips out of service',
                    trips.hidden + short, source.name)
    load_shapes(conn=conn, source=source,
                modes=shape_modes(conn=conn, source=source))
    conn.commit()
    return len(trips.inserted)


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


def load_all(*, sources: list[Source], window: Window,
             scratch: Path) -> MergeReport:
    """
    Load every source into a new file and merge rail duplicates.

    Parameters
    ----------
    sources : list[Source]
        Feeds in load order.
    window : Window
        Dates to cover.
    scratch : Path
        New timetable file.

    Returns
    -------
    MergeReport
        Result of merging rail duplicates.
    """
    with closing(sqlite3.connect(scratch)) as conn:
        create_schema(conn=conn)
        for source in sources:
            print(f'progress load {source.name}', flush=True)
            load_source(conn=conn, source=source, window=window)
        print('progress merge', flush=True)
        report = merge_rail(conn=conn)
        trim_trainlink(conn=conn)
        relabel_trainlink(conn=conn)
        conn.execute(DROP_UNUSED_SHAPES)
        create_indexes(conn=conn)
        write_meta(conn=conn, window=window)
        conn.commit()
    return report


def build_timetable(*, sources: list[Source], window: Window,
                    out: Path, handoff: Handoff | None = None) -> MergeReport:
    """
    Build a timetable file from sources, replacing out only on success.

    Load preferred feeds (Sydney Trains, Metro) before the bundle so that a
    trip ID in both keeps the preferred copy. The run tables are then built
    in a child process, see build_extras.

    Parameters
    ----------
    sources : list[Source]
        Feeds in load order.
    window : Window
        Dates to cover.
    out : Path
        Timetable file to write.
    handoff : Handoff | None
        When set, write the handoff file and exec into the extras program
        instead of returning, so the load and the extras never share
        memory; the extras program finishes the job.

    Returns
    -------
    MergeReport
        Result of merging rail duplicates.
    """
    scratch = out.with_suffix('.new')
    scratch.unlink(missing_ok=True)
    report = load_all(sources=sources, window=window, scratch=scratch)
    gc.collect()
    print(
        f'progress extras parent {current_rss_mb():.0f}MB '
        f'peak {max_rss_mb():.0f}MB',
        flush=True,
    )
    if handoff is not None:
        hand_over(handoff=handoff, record=HandoffRecord(
            scratch=scratch, out=out, dropped=report.dropped,
            partial=report.partial, peak_mb=max_rss_mb(), tail=handoff.tail,
        ))
    build_extras(path=scratch)
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
