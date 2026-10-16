"""Timetable repository over the built SQLite file."""

import sqlite3
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
        it too.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.conn = connection or sqlite3.connect(
            f'file:{path or DEFAULT_PATH}?mode=ro',
            uri=True,
            check_same_thread=False,
        )

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
        row = self.conn.execute(
            'SELECT name FROM stops WHERE stop_id = ?', (stop_id,)
        ).fetchone()
        if row is None:
            raise KeyError(stop_id)
        return str(row[0])

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
        rows = self.conn.execute(
            DEPARTURES,
            {
                'day': day.isoformat(),
                'origin': pin.origin,
                'dest': pin.destination,
                'lo': service_seconds(day=day, moment=start),
                'hi': service_seconds(day=day, moment=end),
            },
        ).fetchall()
        return [to_departure(row=row, day=day) for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()


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
