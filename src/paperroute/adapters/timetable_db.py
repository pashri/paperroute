"""Timetable repository over the built SQLite file."""

import logging
import os
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

from paperroute.core.model import (
    SYDNEY,
    Departure,
    Pin,
    mode_for,
    service_seconds,
    service_time,
)

logger = logging.getLogger(__name__)
DEFAULT_PATH: Final[Path] = Path('/var/lib/paperroute/timetable.db')
DEPARTURES: Final[str] = """
SELECT t.trip_id, r.short_name, r.type, COALESCE(t.agency_id, r.agency_id),
  t.headsign, os.platform,
  o.seq, o.stop_id, MIN(d.seq), d.stop_id, o.dep, d.arr,
  (SELECT group_concat(a.alias_id, ' ') FROM trip_aliases a
     WHERE a.trip_id = t.trip_id)
FROM stop_times o
JOIN trips t ON t.trip_id = o.trip_id
JOIN services sv ON sv.service_id = t.service_id AND sv.date = :day
JOIN routes r ON r.route_id = t.route_id
JOIN stops os ON os.stop_id = o.stop_id
JOIN stop_times d ON d.trip_id = o.trip_id AND d.seq > o.seq
WHERE o.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :origin OR parent_station = :origin)
  AND d.stop_id IN (SELECT stop_id FROM stops
                    WHERE stop_id = :dest OR parent_station = :dest)
  AND o.dep BETWEEN :lo AND :hi AND o.pickup <> 1 AND d.dropoff <> 1
  AND NOT EXISTS (SELECT 1 FROM trip_skip k
                  WHERE k.trip_id = t.trip_id AND k.date = :day)
GROUP BY o.trip_id, o.seq
"""


class SqliteTimetableRepository:
    """
    Read-only queries against the timetable file.

    Parameters
    ----------
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
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.lock = threading.Lock()
        location = path or DEFAULT_PATH
        self.path = None if connection else location
        self.identity: tuple[int, int] | None = None
        if connection:
            self.conn = connection
        else:
            self.conn, self.identity = self.open(path=location)

    def open(
        self, *, path: Path
    ) -> tuple[sqlite3.Connection, tuple[int, int] | None]:
        """
        Open the timetable file read-only.

        The file is identified before it is opened, so a replacement landing
        in between shows up as a change on the next check.

        Parameters
        ----------
        path : Path
            Timetable file.

        Returns
        -------
        tuple[sqlite3.Connection, tuple[int, int] | None]
            New connection, usable from any thread, and the file's
            (st_dev, st_ino).
        """
        identity = file_identity(path=path)
        conn = sqlite3.connect(
            f'file:{path}?mode=ro',
            uri=True,
            check_same_thread=False,
        )
        return conn, identity

    def open_checked(
        self, *, path: Path
    ) -> tuple[sqlite3.Connection, tuple[int, int] | None] | None:
        """
        Open the file and check it reads like a timetable.

        Parameters
        ----------
        path : Path
            Timetable file.

        Returns
        -------
        tuple[sqlite3.Connection, tuple[int, int] | None] | None
            The connection and file identity, or None (after logging a
            warning) when the file can't be used.
        """
        conn = None
        try:
            conn, identity = self.open(path=path)
            conn.execute('SELECT 1 FROM stops LIMIT 1').fetchall()
        except (sqlite3.Error, OSError):
            logger.warning('timetable replacement unusable', exc_info=True)
            if conn is not None:
                conn.close()
            return None
        return conn, identity

    def reopen_if_replaced(self) -> None:
        """
        Swap in a new connection when the file has been replaced.

        Does nothing for an injected connection or a missing file. A
        replacement that can't be read is refused and the old connection
        kept, to be checked again on the next query. The caller must hold
        the lock.
        """
        if self.path is None:
            return
        try:
            current = file_identity(path=self.path)
        except OSError:
            logger.warning('timetable stat failed', exc_info=True)
            return
        if current is None or current == self.identity:
            return
        opened = self.open_checked(path=self.path)
        if opened is None:
            return
        old = self.conn
        self.conn, self.identity = opened
        with closing(old):
            logger.info('timetable reopened')

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
            All rows.
        """
        with self.lock:
            self.reopen_if_replaced()
            return self.conn.execute(sql, params).fetchall()

    def stop_name(self, *, stop_id: str) -> str:
        """
        Name of a stop or station.

        Parameters
        ----------
        stop_id : str
            Stop or station ID.

        Returns
        -------
        str
            Name.

        Raises
        ------
        KeyError
            When the stop isn't in the timetable.
        """
        rows = self.fetch(
            sql='SELECT name FROM stops WHERE stop_id = ?',
            params=(stop_id,),
        )
        if not rows:
            raise KeyError(stop_id)
        return str(rows[0][0])

    def departures(
        self, *, pin: Pin, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        Direct services leaving the pin's From between two moments.

        Service days from the day before start to end's day are searched, so
        trips running past midnight are found.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            In departure order.
        """
        first = start.astimezone(SYDNEY).date()
        last = end.astimezone(SYDNEY).date()
        days = {first - timedelta(days=1), first, last}
        found = [
            departure
            for day in sorted(days)
            for departure in self.on_day(
                pin=pin, day=day, start=start, end=end
            )
        ]
        return sorted(found, key=lambda departure: departure.departs)

    def on_day(
        self, *, pin: Pin, day: date, start: datetime, end: datetime
    ) -> list[Departure]:
        """
        List departures from one service day within the range.

        Parameters
        ----------
        pin : Pin
            From and Destination.
        day : date
            Service date.
        start : datetime
            Earliest departure.
        end : datetime
            Latest departure.

        Returns
        -------
        list[Departure]
            Unsorted departures.
        """
        rows = self.fetch(
            sql=DEPARTURES,
            params={
                'day': day.isoformat(),
                'origin': pin.origin,
                'dest': pin.destination,
                'lo': service_seconds(day=day, moment=start),
                'hi': service_seconds(day=day, moment=end),
            },
        )
        return [to_departure(row=row, day=day) for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        with self.lock:
            self.conn.close()


def file_identity(*, path: Path) -> tuple[int, int] | None:
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


def to_departure(*, row: tuple[object, ...], day: date) -> Departure:
    """
    Turn a query row into a Departure.

    Parameters
    ----------
    row : tuple[object, ...]
        DEPARTURES row.
    day : date
        Service date the row's times count from.

    Returns
    -------
    Departure
        The departure.
    """
    (trip, route, route_type, agency, headsign, platform, oseq, ostop,
     dseq, dstop, dep, arr, aliases) = row
    return Departure(
        trip_id=str(trip),
        aliases=tuple(sorted(str(aliases).split())) if aliases else (),
        route=str(route),
        mode=mode_for(
            route_type=int(str(route_type)),
            agency_id=str(agency) if agency else None,
        ),
        headsign=str(headsign),
        platform=str(platform),
        origin_seq=int(str(oseq)),
        origin_stop=str(ostop),
        destination_seq=int(str(dseq)),
        destination_stop=str(dstop),
        departs=service_time(day=day, seconds=int(str(dep))),
        arrives=service_time(day=day, seconds=int(str(arr))),
        service_day=day,
    )
