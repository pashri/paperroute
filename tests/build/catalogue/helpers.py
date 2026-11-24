"""Shared helpers for the catalogue tests."""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from paperroute_sydney.city import SYDNEY
from paperroute_transit.build.catalogue import CATALOGUE_SCHEMA
from paperroute_transit.build.runs import RUN_SCHEMA
from paperroute_transit.build.timetable import create_schema
from paperroute_transit.core.modes import replacing_modes

BY_NUMBER: Final[frozenset[str]] = replacing_modes(modes=SYDNEY.modes)


@dataclass(frozen=True)
class RouteRow:
    """One routes row and its route_patterns rows for a tiny timetable."""

    route_id: str
    number: str
    long_name: str
    route_type: int = 700
    agency: str | None = 'A1'
    source: str = 'bundle'
    patterns: tuple[tuple[str, tuple[str, ...], int], ...] = (
        ('B', ('S1', 'S2'), 1),
    )


def tiny_db(*, path: Path, routes: Sequence[RouteRow]) -> sqlite3.Connection:
    """
    Write a timetable holding only stops, routes and route_patterns.

    Parameters
    ----------
    path : Path
        Database file.
    routes : Sequence[RouteRow]
        Routes and their patterns; every station named sits near Sydney.

    Returns
    -------
    sqlite3.Connection
        Open connection.
    """
    conn = sqlite3.connect(path)
    create_schema(conn=conn)
    conn.executescript(RUN_SCHEMA)
    conn.executescript(CATALOGUE_SCHEMA)
    stations = {
        station
        for route in routes
        for _, sequence, _ in route.patterns
        for station in sequence
    }
    conn.executemany(
        "INSERT INTO stops VALUES (?, ?, NULL, -33.87, 151.2, '')",
        ((station, station) for station in sorted(stations)),
    )
    conn.executemany(
        'INSERT INTO routes VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)',
        (
            (r.route_id, r.number, r.long_name, r.route_type, r.agency,
             r.source)
            for r in routes
        ),
    )
    conn.executemany(
        'INSERT INTO route_patterns VALUES (?, ?, ?, ?)',
        (
            (r.route_id, headsign, json.dumps(sequence), trips)
            for r in routes
            for headsign, sequence, trips in r.patterns
        ),
    )
    return conn


def chooser_rows(*, conn: sqlite3.Connection, kind: str
                 ) -> list[tuple[str, ...]]:
    """
    Read one kind's chooser rows in sort order.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the catalogue.
    kind : str
        Route kind.

    Returns
    -------
    list[tuple[str, ...]]
        Number, name, tag and line of each row.
    """
    return conn.execute(
        'SELECT number, name, tag, line FROM chooser_routes '
        'WHERE kind = ? ORDER BY sort', (kind,),
    ).fetchall()
