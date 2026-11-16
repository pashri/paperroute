"""Place constants, timetable builders and readers for the trim tests."""

import sqlite3
from collections.abc import Sequence
from typing import Final

from paperroute_transit.build.timetable import create_schema

NEAR: Final[tuple[str, float, float]] = ('near', 0.1300, 52.2060)
BEDFORD: Final[tuple[str, float, float]] = ('bedford', -0.4717, 52.1373)
HAVERHILL_EDGE: Final[tuple[str, float, float]] = ('edge', 0.4950, 52.0900)
NORWICH: Final[tuple[str, float, float]] = ('norwich', 1.2929, 52.6237)
STANSTED: Final[tuple[str, float, float]] = ('stansted', 0.2632, 51.8887)
TRUMPINGTON: Final[tuple[str, float, float]] = (
    'trumpington',
    0.1085,
    52.1680,
)
PARKSIDE: Final[tuple[str, float, float]] = ('parkside', 0.1300, 52.2030)
MILDENHALL: Final[tuple[str, float, float]] = ('mildenhall', 0.5122, 52.3431)

OPENED: Final[list[sqlite3.Connection]] = []

Place = tuple[str, float | None, float | None]


def new_connection() -> sqlite3.Connection:
    """
    Open an empty in-memory timetable that the test run will close.

    Returns
    -------
    sqlite3.Connection
        The database, with the timetable tables made.
    """
    conn = sqlite3.connect(':memory:')
    OPENED.append(conn)
    create_schema(conn=conn)
    return conn


def loaded(
    *,
    stops: Sequence[Place],
    routes: Sequence[tuple[str, int, str, str]],
    trips: Sequence[tuple[str, str]],
    calls: Sequence[tuple[str, str]],
    parent_of: dict[str, str] | None = None,
) -> sqlite3.Connection:
    """
    Build a timetable from plain rows.

    Parameters
    ----------
    stops : Sequence[Place]
        Rows of stop id, longitude and latitude.
    routes : Sequence[tuple[str, int, str, str]]
        Rows of route id, route type, agency id and short name.
    trips : Sequence[tuple[str, str]]
        Rows of trip id and route id.
    calls : Sequence[tuple[str, str]]
        Rows of trip id and stop id, in call order.
    parent_of : dict[str, str] | None
        Parent station by stop id.

    Returns
    -------
    sqlite3.Connection
        The database.
    """
    conn = new_connection()
    parent_of = parent_of or {}
    conn.executemany(
        "INSERT INTO stops VALUES (?, ?, ?, ?, ?, '')",
        [
            (stop_id, stop_id, parent_of.get(stop_id), lat, lon)
            for stop_id, lon, lat in stops
        ],
    )
    conn.executemany(
        "INSERT INTO routes VALUES (?, ?, '', ?, NULL, NULL, ?, 'bods')",
        [
            (route_id, short_name, route_type, agency_id)
            for route_id, route_type, agency_id, short_name in routes
        ],
    )
    conn.executemany(
        "INSERT INTO trips VALUES (?, ?, 's', '', NULL, NULL, NULL, 'bods', "
        'NULL, 1)',
        trips,
    )
    conn.executemany(
        'INSERT INTO stop_times VALUES (?, ?, ?, 0, 0, 0, 0)',
        [
            (trip_id, seq, stop_id)
            for seq, (trip_id, stop_id) in enumerate(calls)
        ],
    )
    conn.commit()
    return conn


def loaded_stops(*rows: tuple[str, str, float, float]) -> sqlite3.Connection:
    """
    Build a timetable that holds stops only.

    Parameters
    ----------
    *rows : tuple[str, str, float, float]
        Rows of stop id, name, longitude and latitude.

    Returns
    -------
    sqlite3.Connection
        The database.
    """
    conn = new_connection()
    conn.executemany(
        "INSERT INTO stops VALUES (?, ?, NULL, ?, ?, '')",
        [(stop_id, name, lat, lon) for stop_id, name, lon, lat in rows],
    )
    conn.commit()
    return conn


def coach_trip(*places: Place) -> sqlite3.Connection:
    """
    Build one coach route with one trip calling at the places in order.

    Parameters
    ----------
    *places : Place
        Stops, as stop id, longitude and latitude.

    Returns
    -------
    sqlite3.Connection
        The database, with route ``bods:c`` and trip ``c1``.
    """
    return loaded(
        stops=sorted(set(places), key=lambda place: place[0]),
        routes=(('bods:c', 200, 'OP5050', 'C'),),
        trips=(('c1', 'bods:c'),),
        calls=[('c1', place[0]) for place in places],
    )


def calls_of(*, conn: sqlite3.Connection, trip: str) -> list[str]:
    """
    Read a trip's stop ids in call order.

    Parameters
    ----------
    conn : sqlite3.Connection
        The database.
    trip : str
        Trip id.

    Returns
    -------
    list[str]
        Stop ids.
    """
    rows = conn.execute(
        'SELECT stop_id FROM stop_times WHERE trip_id = ? ORDER BY seq',
        (trip,),
    )
    return [stop_id for (stop_id,) in rows]


def route_ids(conn: sqlite3.Connection) -> set[str]:
    """
    Read the route ids.

    Parameters
    ----------
    conn : sqlite3.Connection
        The database.

    Returns
    -------
    set[str]
        Route ids.
    """
    return {row[0] for row in conn.execute('SELECT route_id FROM routes')}


def stop_ids(conn: sqlite3.Connection) -> set[str]:
    """
    Read the stop ids.

    Parameters
    ----------
    conn : sqlite3.Connection
        The database.

    Returns
    -------
    set[str]
        Stop ids.
    """
    return {row[0] for row in conn.execute('SELECT stop_id FROM stops')}


def parents(conn: sqlite3.Connection) -> dict[str, str | None]:
    """
    Read each stop's parent station.

    Parameters
    ----------
    conn : sqlite3.Connection
        The database.

    Returns
    -------
    dict[str, str | None]
        Parent station id by stop id.
    """
    rows = conn.execute('SELECT stop_id, parent_station FROM stops')
    return {stop_id: parent for stop_id, parent in rows}


def stop_row(
    conn: sqlite3.Connection,
    stop_id: str,
) -> tuple[str, float, float, str]:
    """
    Read one stop's name, longitude, latitude and platform.

    Parameters
    ----------
    conn : sqlite3.Connection
        The database.
    stop_id : str
        Stop id.

    Returns
    -------
    tuple[str, float, float, str]
        The stop's name, longitude, latitude and platform.
    """
    row: tuple[str, float, float, str] = conn.execute(
        'SELECT name, lon, lat, platform FROM stops WHERE stop_id = ?',
        (stop_id,),
    ).fetchone()
    return row
