"""Shared state and schema probing for the timetable repository."""

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import batched
from pathlib import Path
from typing import Final

from paperroute_device.adapters.sqlite_ro import (
    Identity,
    ReadOnlyDatabase,
    file_identity,
)
from paperroute_transit.adapters.alert_rows import LineMemory
from paperroute_transit.adapters.calendar_rows import read_holidays
from paperroute_transit.core.city import City
from paperroute_transit.core.geo import Point, Projection, to_metres
from paperroute_transit.core.model import SOURCE_DATE, SOURCE_END

DEFAULT_PATH: Final[Path] = Path('/opt/paperroute/data/timetable.db')
CHUNK: Final[int] = 500
LABEL_LIMIT: Final[int] = 20_000
NO_SHAPE: Final[str] = 'NULL'
STOP_ALONG: Final[str] = 'st.along'
NO_ALONG: Final[str] = 'NULL'
MATCH_RADIUS: Final[float] = 100.0  # metres
SCHEMA_CHECK: Final[str] = """
SELECT
  (SELECT COUNT(*) FROM pragma_table_info('trips')
   WHERE name = 'shape_id'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'shapes'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'trip_next'),
  (SELECT COUNT(*) FROM pragma_table_info('trip_next')
   WHERE name = 'next_trip'),
  (SELECT COUNT(*) FROM pragma_table_info('trips')
   WHERE name = 'service'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'trip_joins'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'rail_hops'),
  (SELECT COUNT(*) FROM pragma_table_info('stop_times')
   WHERE name = 'along'),
  (SELECT COUNT(*) FROM sqlite_master
   WHERE type = 'table' AND name = 'trip_turns')
"""
NEXT_COLUMNS: Final[str] = 'n.route, n.headsign, n.next_trip'
OLD_NEXT_COLUMNS: Final[str] = 'n.route, n.headsign, NULL'
SERVICE_ONLY: Final[str] = 'AND t.service = 1'
NEXT_JOIN: Final[str] = 'LEFT JOIN trip_next n ON n.trip_id = t.trip_id'
FROM_CALLS: Final[str] = """
SELECT c.trip_id, c.seq FROM stop_times c
WHERE c.trip_id IN ({marks})
  AND c.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = ? OR parent_station = ?)
  AND NOT (c.pickup = 1 AND c.dropoff = 1)
  AND NOT EXISTS (
    SELECT 1 FROM stop_times p
    WHERE p.trip_id = c.trip_id
      AND p.seq = (SELECT MAX(q.seq) FROM stop_times q
                   WHERE q.trip_id = c.trip_id AND q.seq < c.seq)
      AND p.stop_id IN (SELECT stop_id FROM stops
                        WHERE stop_id = ? OR parent_station = ?))
ORDER BY c.trip_id, c.seq
"""
NO_NEXT: Final[str] = 'NULL, NULL, NULL'
NAMED_NEAR: Final[str] = """
SELECT stop_id, lon, lat FROM stops
WHERE name = :name AND lon BETWEEN :west AND :east
  AND lat BETWEEN :south AND :north
"""
TRIP_COLUMNS: Final[str] = """
t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  {shape}, t.direction_id, t.route_id
"""
BY_TRIP: Final[str] = (
    'SELECT t.trip_id, ' + TRIP_COLUMNS + """
FROM trips t JOIN routes r ON r.route_id = t.route_id
WHERE t.trip_id IN ({marks})
"""
)
BY_ALIAS: Final[str] = (
    'SELECT a.alias_id, ' + TRIP_COLUMNS + """
FROM trip_aliases a JOIN trips t ON t.trip_id = a.trip_id
JOIN routes r ON r.route_id = t.route_id
WHERE a.alias_id IN ({marks})
ORDER BY a.alias_id, t.trip_id
"""
)
SHAPES_BY_ID: Final[str] = (
    'SELECT shape_id, points FROM shapes WHERE shape_id IN ({marks})'
)
RAIL_HOPS_FROM: Final[str] = """
SELECT a, b, path FROM rail_hops
WHERE inside = 1 AND path IS NOT NULL AND a IN ({marks})
"""
MODES_NEAR: Final[str] = """
SELECT DISTINCT mode FROM shapes
WHERE west <= :east AND east >= :west
  AND south <= :north AND north >= :south
"""


@dataclass(frozen=True)
class Features:
    """
    Which optional parts of the schema a timetable file has.

    Attributes
    ----------
    shape_column : bool
        Whether trips has a shape_id column.
    shapes : bool
        Whether the shapes table exists.
    carry_ons : bool
        Whether the trip_next table exists.
    next_trips : bool
        Whether trip_next has the next_trip column.
    service : bool
        Whether trips has the service column.
    joins : bool
        Whether the trip_joins table exists.
    hops : bool
        Whether the rail_hops table exists.
    stop_along : bool
        Whether stop_times has the along column.
    turns : bool
        Whether the trip_turns table exists.
    """

    shape_column: bool = False
    shapes: bool = False
    carry_ons: bool = False
    next_trips: bool = False
    service: bool = False
    joins: bool = False
    hops: bool = False
    stop_along: bool = False
    turns: bool = False

    @property
    def next_columns(self) -> str:
        """
        The carry-on columns a departure query selects.

        Returns
        -------
        str
            Next route, headsign and trip, NULL where the file lacks them.
        """
        if not self.carry_ons:
            return NO_NEXT
        return NEXT_COLUMNS if self.next_trips else OLD_NEXT_COLUMNS


class TimetableBase:
    """
    Connection, schema probing and meta reads for the timetable file.

    Parameters
    ----------
    city : City
        The city: its time zone (the timetable's service days are in it),
        its projection (stop points are placed in it), its route
        classifier and the route ID prefixes its alert feeds need.
    path : Path | None
        Timetable file; defaults to the device location.
    connection : sqlite3.Connection | None
        An open connection to use instead of opening path. close() closes
        it too. A repository given a connection never reopens.

    Notes
    -----
    When built with a path, the file's identity is checked before each
    query and the connection is reopened if the file was replaced. A lock
    serialises queries and the reopen, so a connection is never closed
    while another thread is querying it.
    """

    def __init__(
        self,
        *,
        city: City,
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.zone = city.zone
        self.projection = city.projection
        self.classify = city.classify
        self.route_prefixes = city.alerts.route_prefixes
        self.city = city
        self.db = ReadOnlyDatabase(
            path=path or DEFAULT_PATH,
            connection=connection,
            probe='SELECT 1 FROM stops LIMIT 1',
            label='timetable',
            allow_missing=True,
        )
        self.schema: tuple[Identity | None, Features] | None = None
        self.labels: dict[str, str] = {}
        self.labels_id: Identity | None = None
        self.lines = LineMemory()

    @property
    def identity(self) -> tuple[int, int] | None:
        """
        The identity of the file the open connection reads.

        Returns
        -------
        tuple[int, int] | None
            (st_dev, st_ino), or None when there is no file.
        """
        return self.db.identity

    def fetch(
        self, *, sql: str, params: tuple[object, ...] | dict[str, object]
    ) -> list[tuple[object, ...]]:
        """
        Run a query against the current file.

        Parameters
        ----------
        sql : str
            Query.
        params : tuple[object, ...] | dict[str, object]
            Query parameters.

        Returns
        -------
        list[tuple[object, ...]]
            All rows.
        """
        return self.db.fetch(sql=sql, params=params)

    def fetch_in(
        self,
        *,
        sql: str,
        ids: Collection[str],
        lead: tuple[object, ...] = (),
        tail: tuple[object, ...] = (),
        shape: str = NO_SHAPE,
        along: str = NO_ALONG,
    ) -> list[tuple[object, ...]]:
        """
        Run a query with an IN list, in chunks.

        Parameters
        ----------
        sql : str
            Query with a ``{marks}`` placeholder for the IN list and, when
            it selects a shape, a ``{shape}`` placeholder.
        ids : Collection[str]
            Values for the IN list.
        lead : tuple[object, ...]
            Parameters that come before the IN list.
        tail : tuple[object, ...]
            Parameters that come after the IN list.
        shape : str
            Expression to select in place of ``{shape}``.
        along : str
            Expression to select in place of ``{along}``.

        Returns
        -------
        list[tuple[object, ...]]
            Rows of every chunk, with each chunk's IDs in order.
        """
        rows: list[tuple[object, ...]] = []
        for chunk in batched(sorted(set(ids)), CHUNK):
            query = sql.format(
                marks=','.join('?' * len(chunk)),
                shape=shape,
                along=along,
            )
            rows.extend(
                self.fetch(sql=query, params=(*lead, *chunk, *tail)),
            )
        return rows

    def features(self) -> Features:
        """
        Say which optional tables the file has, after any replacement.

        The answer is kept while the file is unchanged on disk. When it
        may have changed, a query first lets the database reopen it, and
        the answer is worked out again for the file then open.

        Returns
        -------
        Features
            What the file has; nothing for a file built before shapes.
        """
        path = self.db.path
        known = self.schema
        if known is not None and (
            path is None or file_identity(path=path) == known[0]
        ):
            return known[1]
        self.fetch(sql='SELECT 1', params=())
        if known is None or known[0] != self.identity:
            rows = self.fetch(sql=SCHEMA_CHECK, params=())
            found = rows[0] if rows else (0, 0, 0, 0, 0, 0, 0, 0, 0)
            known = (
                self.identity,
                Features(
                    shape_column=bool(found[0]),
                    shapes=bool(found[1]),
                    carry_ons=bool(found[2]),
                    next_trips=bool(found[3]),
                    service=bool(found[4]),
                    joins=bool(found[5]),
                    hops=bool(found[6]),
                    stop_along=bool(found[7]),
                    turns=bool(found[8]),
                ),
            )
            self.schema = known
        return known[1]

    def shape_column(self) -> str:
        """
        The expression that selects a trip's shape ID.

        Returns
        -------
        str
            ``t.shape_id``, or NULL for a file without the column.
        """
        return 't.shape_id' if self.features().shape_column else NO_SHAPE

    def service_filter(self) -> str:
        """
        The clause that keeps out-of-service trips off a board.

        Returns
        -------
        str
            ``AND t.service = 1``, or nothing for a file without the
            column, which has no such trips.
        """
        return SERVICE_ONLY if self.features().service else ''

    def version(self) -> str | None:
        """
        Say which build the file is.

        Returns
        -------
        str | None
            The build time, or None when the file doesn't record one.
        """
        rows = self.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('built_at',),
        )
        return str(rows[0][0]) if rows else None

    def schema_version(self) -> str | None:
        """
        Say which schema the file is built to.

        Returns
        -------
        str | None
            The schema version, or None when the file doesn't record one.
        """
        rows = self.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('schema_version',),
        )
        return str(rows[0][0]) if rows else None

    def origin(self) -> str | None:
        """
        Say which projection origin the file's metres are from.

        Returns
        -------
        str | None
            The origin text, or None when the file doesn't record one.
        """
        rows = self.fetch(
            sql='SELECT value FROM meta WHERE key = ?',
            params=('origin',),
        )
        return str(rows[0][0]) if rows else None

    def last_day(self) -> date | None:
        """
        Say the last day the file has service for every source.

        That is the earliest of the window's last day and each source's
        last calendar date, so a source whose stored feed runs out sooner
        brings the day forward. A file without the source dates gives the
        window's last day; a malformed source date is skipped.

        Returns
        -------
        date | None
            The last day; None when the file records no readable window.
        """
        rows = {
            str(key): str(value)
            for key, value in self.fetch(
                sql=(
                    'SELECT key, value FROM meta'
                    " WHERE key IN ('window_start', 'window_days')"
                    ' OR key LIKE ?'
                ),
                params=(f'{SOURCE_END}%',),
            )
        }
        window = window_end(rows=rows)
        if window is None:
            return None
        return min([window, *source_end_days(rows=rows)])

    def source_dates(self) -> dict[str, date]:
        """
        Say the date each source's feed was published.

        Returns
        -------
        dict[str, date]
            Source name to its feed date; empty when the file records
            none, and a malformed date is skipped.
        """
        rows = {
            str(key): str(value)
            for key, value in self.fetch(
                sql='SELECT key, value FROM meta WHERE key LIKE ?',
                params=(f'{SOURCE_DATE}%',),
            )
        }
        return source_date_days(rows=rows)

    def holidays(self) -> frozenset[date]:
        """
        The public holidays the file records.

        Returns
        -------
        frozenset[date]
            The holidays; none for a file built without them.
        """
        return read_holidays(
            fetch=lambda sql: self.fetch(sql=sql, params=()),
        )



    def close(self) -> None:
        """Close the database connection."""
        self.db.close()




def to_point(
    *,
    lon: object,
    lat: object,
    projection: Projection,
) -> Point | None:
    """
    Project a stop's coordinates.

    Parameters
    ----------
    lon : object
        Longitude column.
    lat : object
        Latitude column.
    projection : Projection
        The projection to place the stop in.

    Returns
    -------
    Point | None
        Metres from the projection origin, or None when either is NULL.
    """
    if lon is None or lat is None:
        return None
    return to_metres(
        lon=float(str(lon)),
        lat=float(str(lat)),
        projection=projection,
    )


def window_end(*, rows: dict[str, str]) -> date | None:
    """
    Work out the last day of a timetable's window from its meta.

    Parameters
    ----------
    rows : dict[str, str]
        Meta keys and values.

    Returns
    -------
    date | None
        The window's start plus its days, less one; None when either is
        missing or malformed.
    """
    try:
        start = date.fromisoformat(rows['window_start'])
        days = timedelta(days=int(rows['window_days']) - 1)
        return start + days
    except (KeyError, ValueError, OverflowError):
        return None


def source_end_days(*, rows: dict[str, str]) -> list[date]:
    """
    Read each source's last service date from a timetable's meta.

    Parameters
    ----------
    rows : dict[str, str]
        Meta keys and values.

    Returns
    -------
    list[date]
        The dates that parse; malformed ones are skipped.
    """
    days = []
    for key, value in rows.items():
        if not key.startswith(SOURCE_END):
            continue
        try:
            days.append(date.fromisoformat(value))
        except ValueError:
            continue
    return days


def source_date_days(*, rows: dict[str, str]) -> dict[str, date]:
    """
    Read each source's feed date from a timetable's meta.

    Parameters
    ----------
    rows : dict[str, str]
        Meta keys and values.

    Returns
    -------
    dict[str, date]
        Source name to its date; malformed dates are skipped.
    """
    days = {}
    for key, value in rows.items():
        if not key.startswith(SOURCE_DATE):
            continue
        try:
            days[key.removeprefix(SOURCE_DATE)] = date.fromisoformat(value)
        except ValueError:
            continue
    return days
