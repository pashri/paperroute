"""Tests for the stations each train hop passes."""

import json
import math
import sqlite3
from array import array
from contextlib import closing
from typing import Final

import pytest

from paperroute_transit.build.passed import (
    PassedReport,
    never_passed,
    write_passed,
)
from paperroute_transit.build.track_db import RailTrack, TrackStation
from paperroute_transit.build.track_paths import SNAP
from paperroute_transit.core.geo import (
    Point,
    Projection,
    to_lonlat,
    unpack_shape,
)

PROJECTION: Final[Projection] = Projection(
    origin_lon=0.0, origin_lat=0.0, grid_lon=0.0, grid_lat=0.0,
)
KINDS: Final[tuple[str, ...]] = ('train',)
NODES: Final[int] = 41
SPACING: Final[float] = 100.0  # m
SITES: Final[dict[str, Point]] = {
    'A': (0.0, 20.0),
    'B': (1000.0, 0.0),
    'C': (2000.0, 0.0),
    'D': (3000.0, 20.0),
    'T': (1500.0, 0.0),
    'X': (500.0, 1400.0),
    'Y': (600.0, 1400.0),
    'Q': (0.0, 4000.0),
    'N': (-500.0, 5000.0),
    'M': (3500.0, 5000.0),
}
LINE: Final[RailTrack] = RailTrack(
    xs=array('d', (SPACING * i for i in range(NODES))),
    ys=array('d', (0.0 for _ in range(NODES))),
    edges=array('q', (n for i in range(NODES - 1) for n in (i, i + 1))),
    stations=(),
)


def osm_station(*, osm_id: int, x: float) -> TrackStation:
    """
    Place an OSM station on the line.

    Parameters
    ----------
    osm_id : int
        The OSM node id.
    x : float
        Metres east of the origin.

    Returns
    -------
    TrackStation
        A station named ``Osm <id>``.
    """
    lon, lat = to_lonlat(point=(x, 10.0), projection=PROJECTION)
    return TrackStation(osm_id, f'Osm {osm_id}', lat, lon, x, 10.0)


def timetable_of(
    *,
    runs: list[tuple[str, str]],
    stops: dict[int, str] | None = None,
    patterns: list[str] | None = None,
    trains: dict[int, int] | None = None,
) -> sqlite3.Connection:
    """
    Build a timetable with runs and the stations they name.

    Parameters
    ----------
    runs : list[tuple[str, str]]
        Kind and space-separated station ids of each run.
    stops : dict[int, str] | None
        Stopping calls by run position (1-based) where they differ from
        the run's stations.
    patterns : list[str] | None
        Trip patterns; one per run when None.
    trains : dict[int, int] | None
        Regular weekday trains by run position (1-based); a run not
        listed has no ``weekday_runs`` row.

    Returns
    -------
    sqlite3.Connection
        An in-memory database with the run, call, pattern, weekday and
        stop tables.
    """
    conn = sqlite3.connect(':memory:')
    conn.execute(
        'CREATE TABLE runs (run_id INTEGER PRIMARY KEY, kind TEXT,'
        ' route TEXT, stations TEXT, trips INTEGER)',
    )
    conn.execute(
        'CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT,'
        ' parent_station TEXT, lat REAL, lon REAL, platform TEXT)',
    )
    conn.execute(
        'CREATE TABLE run_calls (run_id INTEGER, stops TEXT,'
        ' no_board TEXT, no_alight TEXT)',
    )
    conn.execute(
        'CREATE TABLE route_patterns (route_id TEXT, headsign TEXT,'
        ' stations TEXT, trips INTEGER)',
    )
    conn.execute(
        'CREATE TABLE routes (route_id TEXT PRIMARY KEY,'
        ' short_name TEXT)',
    )
    conn.execute(
        'CREATE TABLE weekday_runs (run_id INTEGER PRIMARY KEY,'
        ' trains INTEGER NOT NULL)',
    )
    conn.execute("INSERT INTO routes VALUES ('r1', 'r')")
    conn.executemany(
        'INSERT INTO weekday_runs VALUES (?, ?)', (trains or {}).items(),
    )
    for number, (kind, stations) in enumerate(runs, start=1):
        conn.execute(
            'INSERT INTO runs (kind, route, stations, trips)'
            ' VALUES (?, ?, ?, ?)',
            (kind, 'r', json.dumps(stations.split()), 1),
        )
        conn.execute(
            'INSERT INTO run_calls VALUES (?, ?, ?, ?)',
            (
                number,
                json.dumps((stops or {}).get(number, stations).split()),
                '[]',
                '[]',
            ),
        )
    for pattern in patterns or [stations for _, stations in runs]:
        conn.execute(
            "INSERT INTO route_patterns VALUES ('r1', 'h', ?, 1)",
            (json.dumps(pattern.split()),),
        )
    for stop_id, point in SITES.items():
        lon, lat = to_lonlat(point=point, projection=PROJECTION)
        conn.execute(
            'INSERT INTO stops VALUES (?, ?, ?, ?, ?, ?)',
            (stop_id, f'Stop {stop_id}', '', lat, lon, ''),
        )
    return conn


def passed_by_hop(
    *, conn: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[list[str], int, bytes | None]]:
    """
    Read the hops written.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.

    Returns
    -------
    dict[tuple[str, str], tuple[list[str], int, bytes | None]]
        Passed stations, inside flag and path by hop.
    """
    return {
        (a, b): (json.loads(passed), inside, path)
        for a, b, passed, inside, path in conn.execute(
            'SELECT a, b, passed, inside, path FROM rail_hops',
        )
    }


def run_passed(
    *,
    runs: list[tuple[str, str]],
    stations: tuple[TrackStation, ...] = (),
    track: bool = True,
    patterns: list[str] | None = None,
    stops: dict[int, str] | None = None,
    trains: dict[int, int] | None = None,
) -> tuple[
    sqlite3.Connection,
    PassedReport,
]:
    """
    Write the passed tables for runs over the line.

    Parameters
    ----------
    runs : list[tuple[str, str]]
        Kind and stations of each run.
    stations : tuple[TrackStation, ...]
        OSM stations on the line.
    track : bool
        False to build with no track.
    patterns : list[str] | None
        Trip patterns, see ``timetable_of``.
    stops : dict[int, str] | None
        Stopping calls by run position, see ``timetable_of``.
    trains : dict[int, int] | None
        Weekday trains by run position, see ``timetable_of``.

    Returns
    -------
    tuple[sqlite3.Connection, PassedReport]
        The timetable and the report.
    """
    conn = timetable_of(
        runs=runs, stops=stops, trains=trains, patterns=patterns,
    )
    report = write_passed(
        conn=conn,
        track=(
            RailTrack(
                xs=LINE.xs, ys=LINE.ys, edges=LINE.edges, stations=stations,
            )
            if track
            else None
        ),
        kinds=KINDS,
        projection=PROJECTION,
    )
    return conn, report


def test_a_fast_train_lists_the_stations_it_runs_through() -> None:
    """A non-stop A to D lists B and C, which other trains call at."""
    conn, report = run_passed(runs=[
        ('train', 'A D'), ('train', 'N B C M'),
    ])
    with closing(conn):
        passed, inside, _ = passed_by_hop(conn=conn)[('A', 'D')]
    assert (passed, inside) == (['B', 'C'], 1)
    assert (report.hops, report.inside) == (4, 2)


def test_a_waypoint_comes_in_track_order() -> None:
    """Waypoints follow the track, not their ids: T comes before C."""
    conn, _ = run_passed(runs=[
        ('train', 'A D'), ('train', 'A C D'), ('train', 'A T D'),
    ], trains={2: 6, 3: 6})
    with closing(conn):
        passed, inside, _ = passed_by_hop(conn=conn)[('A', 'D')]
    assert (passed, inside) == (['T', 'C'], 1)


def test_a_waypoint_too_far_off_the_line_is_ignored() -> None:
    """A station called between A and D far off the line is no waypoint."""
    conn, _ = run_passed(
        runs=[('train', 'A D'), ('train', 'A Q D')], trains={2: 6},
    )
    with closing(conn):
        passed, inside, _ = passed_by_hop(conn=conn)[('A', 'D')]
    assert (passed, inside) == ([], 1)


def test_a_feed_marked_pass_has_hops_by_calls_and_by_stations() -> None:
    """A run A, B, D that passes B has A-D, A-B and B-D rows."""
    conn, _ = run_passed(
        runs=[('train', 'A B D')], stops={1: 'A D'}, trains={1: 6},
    )
    with closing(conn):
        rows = passed_by_hop(conn=conn)
    assert set(rows) == {('A', 'D'), ('A', 'B'), ('B', 'D')}
    assert rows[('A', 'D')][:2] == (['B'], 1)
    assert rows[('A', 'B')][:2] == ([], 1)


def test_a_terminus_chained_into_a_run_is_never_passed() -> None:
    """A station every trip starts or ends at stays in the never set."""
    runs = [('train', 'A B C')]
    with closing(timetable_of(runs=runs, patterns=['A B', 'B C'])) as conn:
        assert never_passed(conn=conn, kinds=KINDS) == {'A', 'B', 'C'}
    with closing(
        timetable_of(runs=runs, patterns=['A B', 'B C', 'A B C']),
    ) as conn:
        assert never_passed(conn=conn, kinds=KINDS) == {'A', 'C'}


def test_a_terminus_waypoint_is_never_passed() -> None:
    """A waypoint that every trip starts or ends at is not listed."""
    conn, _ = run_passed(
        runs=[('train', 'A D'), ('train', 'A C D')],
        patterns=['A D', 'A C', 'C D'],
        trains={2: 6},
    )
    with closing(conn):
        passed, inside, _ = passed_by_hop(conn=conn)[('A', 'D')]
    assert (passed, inside) == ([], 1)


def test_a_waypoint_from_a_later_trip_is_ignored() -> None:
    """A run that calls at A again before B gives the A-B hop no waypoints."""
    conn, _ = run_passed(runs=[
        ('train', 'A X Y A B'), ('train', 'A B'),
    ], trains={1: 6})
    with closing(conn):
        passed, inside, _ = passed_by_hop(conn=conn)[('A', 'B')]
    assert (passed, inside) == ([], 1)


def test_a_terminus_is_never_passed() -> None:
    """A station that only ever ends a run is not listed as passed."""
    conn, _ = run_passed(runs=[
        ('train', 'A D'), ('train', 'T N'), ('train', 'N B M'),
    ])
    with closing(conn):
        passed, _, _ = passed_by_hop(conn=conn)[('A', 'D')]
        assert never_passed(conn=conn, kinds=KINDS) == {
            'A', 'D', 'T', 'N', 'M',
        }
    assert passed == ['B']


@pytest.mark.parametrize(
    ('x', 'listed'), [(2240.0, []), (2260.0, ['osm:7'])],
)
def test_an_osm_station_near_a_timetable_station_is_not_added(
    x: float, listed: list[str],
) -> None:
    """An OSM station within 250 m of a timetable station is dropped."""
    conn, report = run_passed(
        runs=[('train', 'A D'), ('train', 'N C M')],
        stations=(osm_station(osm_id=7, x=x),),
    )
    with closing(conn):
        passed, _, _ = passed_by_hop(conn=conn)[('A', 'D')]
        places = conn.execute(
            'SELECT station_id, name FROM track_places',
        ).fetchall()
    assert [p for p in passed if p.startswith('osm:')] == listed
    assert places == [(item, 'Osm 7') for item in listed]
    assert report.added == len(listed)


def test_a_hop_off_the_track_is_outside() -> None:
    """A hop with an end far from the track has no stations and no path."""
    conn, report = run_passed(runs=[('train', 'A Q')])
    with closing(conn):
        row = passed_by_hop(conn=conn)[('A', 'Q')]
    assert row == ([], 0, None)
    assert report.inside == 0


def test_the_hop_path_unpacks_to_a_shape() -> None:
    """The path runs from A's track to D's."""
    conn, _ = run_passed(runs=[('train', 'A D')])
    with closing(conn):
        _, _, path = passed_by_hop(conn=conn)[('A', 'D')]
    assert path is not None
    shape = unpack_shape(shape_id='A>D', blob=path)
    first, last = shape.points[0], shape.points[-1]
    assert math.dist(first, SITES['A']) <= SNAP
    assert math.dist(last, SITES['D']) <= SNAP


def test_no_track_builds_with_no_passed_stations() -> None:
    """With no track every hop is outside, and both tables exist."""
    conn, report = run_passed(
        runs=[('train', 'A D'), ('train', 'N B C M')], track=False,
    )
    with closing(conn):
        rows = passed_by_hop(conn=conn)
        places = conn.execute('SELECT * FROM track_places').fetchall()
    assert all(row == ([], 0, None) for row in rows.values())
    assert places == []
    assert report == PassedReport(hops=4, inside=0, added=0)


@pytest.mark.parametrize(
    ('runs', 'trains', 'inside'),
    [
        ([('train', 'A D'), ('train', 'A X D')], {2: 5}, 1),
        ([('train', 'A D'), ('train', 'A X D')], {2: 6}, 0),
        (
            [('train', 'A D'), ('train', 'A X D'), ('train', 'A X D')],
            {2: 3, 3: 3},
            0,
        ),
        ([('train', 'A D'), ('train', 'A X D')], {}, 1),
    ],
    ids=['five', 'six', 'three and three', 'no row'],
)
def test_a_waypoint_needs_six_weekday_trains(
    runs: list[tuple[str, str]], trains: dict[int, int], inside: int,
) -> None:
    """X, off the track, steers A to D only with six trains' support."""
    conn, _ = run_passed(runs=runs, trains=trains)
    with closing(conn):
        passed, flag, _ = passed_by_hop(conn=conn)[('A', 'D')]
    assert (passed, flag) == ([], inside)
