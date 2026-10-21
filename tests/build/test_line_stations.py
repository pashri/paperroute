"""Tests for writing line station lists and station aliases."""

import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute.build.gtfs_files import Source
from paperroute.build.line_stations import Spot, station_aliases
from paperroute.build.timetable import Window, build_timetable
from tests.build.network_feed import network_files
from tests.helpers import write_feed

SEYMOUR: Final[str] = 'Seymour Station'
T1_TAIL: Final[list[tuple[str, str]]] = [
    ('J', ''), ('C', 'E2'), ('E1', ''), ('E2', ''), ('W1', 'W2'),
    ('W2', ''),
]


def built(
    *, tmp_path: Path, files: dict[str, list[dict[str, str]]],
) -> sqlite3.Connection:
    """
    Build a timetable from one Sydney Trains-like feed.

    Parameters
    ----------
    tmp_path : Path
        Directory for the files.
    files : dict[str, list[dict[str, str]]]
        Feed members.

    Returns
    -------
    sqlite3.Connection
        Open connection to the timetable.
    """
    out = tmp_path / 'timetable.db'
    source = Source(name='sydneytrains',
                    archive=write_feed(path=tmp_path / 'n.zip', files=files))
    build_timetable(sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    return sqlite3.connect(out)


def t1_rows(*, conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """
    Read T1's station list.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    list[tuple[str, str]]
        Station and head rows in order.
    """
    return conn.execute(
        'SELECT station_id, head FROM line_stations '
        "WHERE line_key = 'train:T1' ORDER BY pos",
    ).fetchall()


def test_station_aliases_merge_the_same_name_close_by() -> None:
    """A second Seymour 5 m away maps to the busier one."""
    spots = {
        'V1': Spot(name=SEYMOUR, lat=-37.02436, lon=145.13866),
        'S2': Spot(name=SEYMOUR, lat=-37.02441, lon=145.13870),
        'FAR': Spot(name=SEYMOUR, lat=-37.03, lon=145.1387),
        'NEAR': Spot(name='Seymour Yard', lat=-37.02436, lon=145.13866),
    }
    trips = Counter({'V1': 6, 'S2': 3, 'FAR': 1, 'NEAR': 9})
    assert station_aliases(spots=spots, trips=trips) == {'S2': 'V1'}


def test_station_aliases_tie_goes_to_the_smallest_id() -> None:
    """Equally busy same-place stations keep the smaller ID."""
    spots = {
        'B': Spot(name=SEYMOUR, lat=-37.0, lon=145.0),
        'A': Spot(name=SEYMOUR, lat=-37.0, lon=145.0),
    }
    assert station_aliases(
        spots=spots, trips=Counter({'A': 2, 'B': 2}),
    ) == {'B': 'A'}


def test_pass_through_calls_leave_the_station_off(tmp_path: Path) -> None:
    """No T1 call at R2 picks up or sets down: R2 is track, not listed."""
    files = network_files()
    files['stop_times.txt'] = [
        {**row, 'pickup_type': '1', 'drop_off_type': '1'}
        if row['stop_id'] == 'R2_1' else row
        for row in files['stop_times.txt']
    ]
    with closing(built(tmp_path=tmp_path, files=files)) as conn:
        rows = t1_rows(conn=conn)
        track = {station for (station,) in conn.execute(
            "SELECT station_id FROM line_track WHERE line_key = 'train:T1'",
        )}
        exits = conn.execute(
            "SELECT * FROM line_exits WHERE off_id = 'R2'",
        ).fetchall()
    assert rows == [('R3', 'R3'), ('R1', ''), *T1_TAIL]
    assert track == {station for station, _ in rows} | {'R2'}
    assert not exits


def test_same_place_station_is_listed_once(tmp_path: Path) -> None:
    """The 're' trips call at R2X, a second 'R2 Stop': it maps to R2."""
    files = network_files()
    twins = [
        {**row, 'stop_id': row['stop_id'].replace('R2', 'R2X'),
         'parent_station': row['parent_station'] and 'R2X'}
        for row in files['stops.txt']
        if row['stop_id'] in ('R2', 'R2_1')
    ]
    files['stops.txt'] = [*files['stops.txt'], *twins]
    files['stop_times.txt'] = [
        {**row, 'stop_id': 'R2X_1'}
        if row['stop_id'] == 'R2_1' and row['trip_id'].startswith('re')
        else row
        for row in files['stop_times.txt']
    ]
    with closing(built(tmp_path=tmp_path, files=files)) as conn:
        rows = t1_rows(conn=conn)
        aliases = conn.execute('SELECT * FROM line_aliases').fetchall()
    assert rows == [('R3', 'R3'), ('R2', ''), ('R1', ''), *T1_TAIL]
    assert aliases == [('R2X', 'R2')]


def test_an_express_leaves_the_line_where_it_runs_through(
    tmp_path: Path,
) -> None:
    """The 'er' trips run through R1 without stopping: their exit is R1."""
    files = network_files()
    files['stop_times.txt'] = [
        {**row, 'pickup_type': '1', 'drop_off_type': '1'}
        if row['stop_id'] == 'R1_1' and row['trip_id'].startswith('er')
        else row
        for row in files['stop_times.txt']
    ]
    with closing(built(tmp_path=tmp_path, files=files)) as conn:
        exits = conn.execute(
            "SELECT station_id, off_id FROM line_exits "
            "WHERE line_key = 'train:T2' ORDER BY off_id",
        ).fetchall()
    assert exits == [('J', 'R1'), ('J', 'W1')]
