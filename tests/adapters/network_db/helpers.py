"""Shared helpers for the network db tests."""

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Final

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.chooser import ChooserPath, Column, Level
from paperroute_transit.core.city import City
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import RunTail, StripKey

TRAIN: Final[ChooserPath] = ((Level.KIND, 'train'),)


T1: Final[ChooserPath] = (*TRAIN, (Level.LINE, 'train:T1'))


LINE_CITY: Final[City] = replace(
    SYDNEY,
    modes=tuple(replace(m, end_points=False) for m in SYDNEY.modes),
)


type RouteInsert = tuple[str, str, str, int, str, int, str, str, str]


def place(*, stop_id: str) -> StopPlace:
    """
    Make a From place with no point.

    Parameters
    ----------
    stop_id : str
        Stop or station ID.

    Returns
    -------
    StopPlace
        The place.
    """
    return StopPlace(stop_id=stop_id, name=stop_id, point=None)



def add_routes(*, path: Path, rows: Sequence[RouteInsert]) -> None:
    """
    Insert chooser_routes rows.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    rows : Sequence[RouteInsert]
        Route key, kind, group, group order, number, sort, name, tag and
        line.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany(
            'INSERT INTO chooser_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            rows,
        )
        conn.commit()


def add_towards(
    *, path: Path, route_key: str, headsign: str, trips: int,
) -> int:
    """
    Insert one chooser_towards row.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    route_key : str
        Chooser row key.
    headsign : str
        Headsign.
    trips : int
        Trip count.

    Returns
    -------
    int
        The new towards ID.
    """
    with closing(sqlite3.connect(path)) as conn:
        cursor = conn.execute(
            'INSERT INTO chooser_towards (route_key, headsign, trips, '
            "stations) VALUES (?, ?, ?, '[]')",
            (route_key, headsign, trips),
        )
        conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def set_runs(*, path: Path, runs: Sequence[tuple[str, int]]) -> None:
    """
    Replace every run with T1 train runs.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    runs : Sequence[tuple[str, int]]
        Space-separated stations and trip-days of each run; a station
        written '~X' is passed through without stopping, '+X' only
        boarded at and '-X' only left at. The run_calls rows are written
        only on a file with that table, with the one-way positions only
        on a file with those columns.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DELETE FROM runs')
        conn.execute('DELETE FROM run_stops')
        stopping = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'run_calls'",
        ).fetchall())
        one_way = bool(conn.execute(
            "SELECT 1 FROM pragma_table_info('run_calls') "
            "WHERE name = 'no_board'",
        ).fetchall())
        if stopping:
            conn.execute('DELETE FROM run_calls')
        for run_id, (stations, trips) in enumerate(runs, start=1):
            calls = [call.lstrip('~+-') for call in stations.split()]
            stops = [call for call in stations.split() if call[0] != '~']
            conn.execute(
                "INSERT INTO runs VALUES (?, 'train', 'T1', ?, ?)",
                (run_id, json.dumps(calls), trips),
            )
            row = (
                run_id, json.dumps([call.lstrip('+-') for call in stops]),
                *(
                    json.dumps([
                        pos
                        for pos, call in enumerate(stops)
                        if call[0] == mark
                    ])
                    for mark in '-+'
                ),
            )
            if stopping:
                conn.execute(
                    'INSERT INTO run_calls VALUES (?, ?, ?, ?)'
                    if one_way else 'INSERT INTO run_calls VALUES (?, ?)',
                    row if one_way else row[:2],
                )
            conn.executemany(
                'INSERT INTO run_stops VALUES (?, ?, ?)',
                [(station, run_id, pos) for pos, station in enumerate(calls)],
            )
        conn.commit()


def tails_from(
    *, path: Path, station: str, kind: SydneyMode | None = SydneyMode.TRAIN,
    routes: tuple[str, ...] = (),
) -> tuple[RunTail, ...]:
    """
    Read the tails through a station.

    Parameters
    ----------
    path : Path
        Timetable file.
    station : str
        From station ID.
    kind : SydneyMode | None
        Strip kind.
    routes : tuple[str, ...]
        Strip routes.

    Returns
    -------
    tuple[RunTail, ...]
        The reach input's tails.
    """
    with closing(SqliteNetworkRepository(
        path=path,
        city=LINE_CITY,
    )) as repo:
        found = repo.reach_input(
            key=StripKey(station=station, kind=kind, routes=routes),
            origin=place(stop_id=station),
        )
    assert found is not None
    return found.tails


def labels(*, column: Column | None) -> list[str]:
    """
    List a column's labels.

    Parameters
    ----------
    column : Column | None
        Column.

    Returns
    -------
    list[str]
        Labels in order.
    """
    assert column is not None
    return [choice.label for choice in column.items]


def merge_e2_into_r3(*, path: Path) -> None:
    """
    Merge E2 into R3, as line_aliases would, and drop E2 from the lines.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO line_aliases VALUES ('E2', 'R3')")
        conn.execute("DELETE FROM line_stations WHERE station_id = 'E2'")
        conn.commit()


def add_named_lines(*, path: Path) -> None:
    """
    Add two GA lines, named apart, that both serve station C.

    Parameters
    ----------
    path : Path
        Timetable file, changed in place.
    """
    add_routes(path=path, rows=[
        ('train:GA:Norwich', 'train', '', 0, 'GA', 201, 'Norwich', '', ''),
        ('train:GA:Liverpool', 'train', '', 0, 'GA', 202, 'Liverpool', '', ''),
    ])
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany(
            "INSERT INTO line_stations VALUES (?, 0, 'C', '')",
            [('train:GA:Norwich',), ('train:GA:Liverpool',)],
        )
        conn.commit()


def opened(*, path: Path) -> closing[SqliteNetworkRepository]:
    """
    Open a network repository over a timetable file.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    closing[SqliteNetworkRepository]
        Context manager that closes the repository on exit.
    """
    return closing(SqliteNetworkRepository(path=path, city=LINE_CITY))
