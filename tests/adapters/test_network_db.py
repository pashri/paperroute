"""Tests for the network repository: chooser columns, lines and runs."""

import json
import os
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.chooser import Choice, ChooserPath, Column, Level
from paperroute.core.model import RouteKind, StopPlace
from paperroute.core.reach import (
    ReachInput,
    RowKind,
    RunTail,
    StripKey,
    build_reach,
)
from paperroute.core.reach_layout import LineExit
from paperroute.core.strip_view import build_strip_view
from paperroute.ports import NetworkRepository
from tests.build.network_feed import network_feed, network_files
from tests.core.factories import inputs_of
from tests.helpers import old_schema, schema_three, write_feed

REAL_DB: Final[Path] = Path(__file__).parents[2] / 'data' / 'timetable.db'
TOWN_HALL: Final[str] = '200070'
TRAIN: Final[ChooserPath] = ((Level.KIND, 'train'),)
T1: Final[ChooserPath] = (*TRAIN, (Level.LINE, 'train:T1'))
type RouteInsert = tuple[str, str, str, int, str, int, str, str, str]


def has_runs(*, path: Path) -> bool:
    """
    Say whether a timetable file exists and holds the run tables.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    bool
        True when the runs table is there.
    """
    if not path.exists():
        return False
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'runs'",
        ).fetchall()
    return bool(found)


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


def build_network(*, folder: Path) -> Path:
    """
    Build the network feed, with its run tables, over a 3-day window.

    Parameters
    ----------
    folder : Path
        Directory to build in.

    Returns
    -------
    Path
        Timetable file.
    """
    out = folder / 'timetable.db'
    build_timetable(
        sources=[
            Source(
                name='sydneytrains',
                archive=network_feed(path=folder / 'network.zip'),
            ),
        ],
        window=Window(start=date(2026, 10, 5), days=3),
        out=out,
    )
    return out


@pytest.fixture(name='network_db', scope='module')
def _network_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network timetable once for the module.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file.
    """
    return build_network(folder=tmp_path_factory.mktemp('network'))


@pytest.fixture(name='copy')
def _copy(network_db: Path, tmp_path: Path) -> Path:
    """
    A copy of the network timetable that a test may change.

    Parameters
    ----------
    network_db : Path
        Timetable file.
    tmp_path : Path
        Working directory.

    Returns
    -------
    Path
        The copy.
    """
    return Path(shutil.copy(network_db, tmp_path / 'timetable.db'))


@pytest.fixture(name='network')
def _network(network_db: Path) -> Iterator[SqliteNetworkRepository]:
    """
    A repository over the unchanged network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.

    Yields
    ------
    SqliteNetworkRepository
        Repository, closed after the test.
    """
    with closing(SqliteNetworkRepository(path=network_db)) as repo:
        yield repo


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
        Space-separated stations and trip-days of each run.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DELETE FROM runs')
        conn.execute('DELETE FROM run_stops')
        for run_id, (stations, trips) in enumerate(runs, start=1):
            calls = stations.split()
            conn.execute(
                "INSERT INTO runs VALUES (?, 'train', 'T1', ?, ?)",
                (run_id, json.dumps(calls), trips),
            )
            conn.executemany(
                'INSERT INTO run_stops VALUES (?, ?, ?)',
                [(station, run_id, pos) for pos, station in enumerate(calls)],
            )
        conn.commit()


def tails_from(
    *, path: Path, station: str, kind: RouteKind | None = RouteKind.TRAIN,
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
    kind : RouteKind | None
        Strip kind.
    routes : tuple[str, ...]
        Strip routes.

    Returns
    -------
    tuple[RunTail, ...]
        The reach input's tails.
    """
    with closing(SqliteNetworkRepository(path=path)) as repo:
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


def test_kind_column_lists_seven_kinds_in_order(copy: Path) -> None:
    """Every kind with routes is listed, counted, in the chooser order."""
    add_routes(path=copy, rows=[
        ('metro:M1', 'metro', '', 0, 'M1', 1, 'North West', '', ''),
        ('light_rail:L1', 'light_rail', '', 0, 'L1', 1, 'Dulwich', '', ''),
        ('ferry:F1', 'ferry', '', 0, 'F1', 1, 'Manly', '', ''),
        ('school:S1:x', 'school', '1–99', 0, 'S1', 1, 'School', '', ''),
        ('coach:C1:x', 'coach', '', 0, 'C1', 1, 'Far', '', ''),
        ('replacement:1T1:x', 'replacement', '', 0, '1T1', 1, 'B', '',
         'T1'),
    ])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        column = repo.choices(path=())
    assert column == Column(level=Level.KIND, items=(
        Choice(key='bus', label='Bus', badge='B', detail='2 routes'),
        Choice(key='train', label='Train', badge='T', detail='2 lines'),
        Choice(key='metro', label='Metro', badge='M', detail='1 line'),
        Choice(key='light_rail', label='Light rail', badge='L',
               detail='1 line'),
        Choice(key='ferry', label='Ferry', badge='F', detail='1 line'),
        Choice(key='school', label='School', badge='S', detail='1 route'),
        Choice(key='coach', label='Coach', badge='C',
               detail='1 route'),
    ))


def test_kind_column_leaves_out_empty_kinds(
    network: SqliteNetworkRepository,
) -> None:
    """Kinds without routes are not offered."""
    assert labels(column=network.choices(path=())) == ['Bus', 'Train']


def test_bus_path_goes_group_route_towards(copy: Path) -> None:
    """Bus opens hundreds groups, then routes, then towards rows."""
    add_routes(path=copy, rows=[
        ('bus:606:x', 'bus', '600s', 600, '606', 1, 'Parramatta', 'Parra',
         ''),
    ])
    bus: ChooserPath = ((Level.KIND, 'bus'),)
    group = (*bus, (Level.GROUP, '800s'))
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        groups = repo.choices(path=bus)
        six = repo.choices(path=(*bus, (Level.GROUP, '600s')))
        routes = repo.choices(path=group)
        towards = repo.choices(
            path=(*group, (Level.ROUTE, 'bus:811:Line 811')),
        )
    assert groups == Column(level=Level.GROUP, items=(
        Choice(key='600s', label='600s', detail='1'),
        Choice(key='800s', label='800s', detail='2'),
    ))
    assert six == Column(level=Level.ROUTE, items=(
        Choice(key='bus:606:x', label='Parramatta', badge='606',
               tag='Parra'),
    ))
    assert routes is not None
    assert [(c.key, c.badge) for c in routes.items] == [
        ('bus:809:Line 809', '809'), ('bus:811:Line 811', '811'),
    ]
    assert towards is not None
    assert towards.level is Level.TOWARDS
    assert [(c.label, c.detail) for c in towards.items] == [
        ('Parramatta Stn', '1'),
    ]


def test_train_lines_rule_off_intercity(copy: Path) -> None:
    """Lines list in order; a rule sits above the first intercity line."""
    add_routes(path=copy, rows=[
        ('train:BMT', 'train', '', 0, 'BMT', 101, 'Blue Mountains', '', ''),
        ('train:CCN', 'train', '', 0, 'CCN', 102, 'Central Coast', '', ''),
        ('ferry:F1', 'ferry', '', 0, 'F1', 1, 'Manly', '', ''),
    ])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        lines = repo.choices(path=TRAIN)
        ferries = repo.choices(path=((Level.KIND, 'ferry'),))
    assert lines is not None
    assert lines.level is Level.LINE
    assert [(c.badge, c.label, c.rule_above) for c in lines.items] == [
        ('T1', 'Line T1', False), ('T2', 'Line T2', False),
        ('BMT', 'Blue Mountains', True), ('CCN', 'Central Coast', False),
    ]
    assert ferries == Column(level=Level.LINE, items=(
        Choice(key='ferry:F1', label='Manly', badge='F1'),
    ))


def test_towards_busiest_first(copy: Path) -> None:
    """A route's towards rows run from the most trips to the fewest."""
    for headsign, trips in (('Few', 1), ('Most', 9), ('Some', 4)):
        add_towards(
            path=copy, route_key='bus:811:Line 811', headsign=headsign,
            trips=trips,
        )
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        column = repo.choices(path=(
            (Level.KIND, 'bus'), (Level.GROUP, '800s'),
            (Level.ROUTE, 'bus:811:Line 811'),
        ))
    assert column is not None
    assert column.level is Level.TOWARDS
    assert [(c.label, c.detail) for c in column.items] == [
        ('Most', '9'), ('Some', '4'), ('Parramatta Stn', '1'), ('Few', '1'),
    ]


def test_line_column_for_train_has_no_towards(
    network: SqliteNetworkRepository,
) -> None:
    """A train line opens its stations in blocks, R3 first."""
    column = network.choices(path=T1)
    assert column is not None
    assert column.level is Level.STATION
    first, station = column.items[:2]
    assert first == Choice(key='', label='To R3 Stop', heading=True)
    assert (station.key, station.label) == ('R3', 'R3 Stop')
    assert station.place is not None
    assert station.place.point is not None
    rows = [(c.key, c.label, c.heading) for c in column.items]
    assert rows == [
        ('', 'To R3 Stop', True), ('R3', 'R3 Stop', False),
        ('R2', 'R2 Stop', False), ('R1', 'R1 Stop', False),
        ('J', 'J Stop', False), ('', 'To E2 Stop', True),
        ('C', 'C Stop', False), ('E1', 'E1 Stop', False),
        ('E2', 'E2 Stop', False), ('', 'To W2 Stop', True),
        ('W1', 'W1 Stop', False), ('W2', 'W2 Stop', False),
    ]


def test_to_column_marks_from_all_and_dims_unreached(
    network: SqliteNetworkRepository,
) -> None:
    """From W2 the From reads ALL and only the R stations are grey."""
    column = network.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert column.level is Level.TO
    stations = {c.key: c for c in column.items if not c.heading}
    assert stations['W2'].tag == 'ALL'
    assert not stations['W2'].dim
    assert {key for key, c in stations.items() if c.dim} == {
        'R1', 'R2', 'R3',
    }
    assert not stations['C'].dim
    assert [c.label for c in column.items if c.heading] == [
        'To R3 Stop', 'To E2 Stop', 'To W2 Stop',
    ]


def test_to_column_treats_aliases_as_their_kept_station(copy: Path) -> None:
    """A merged station counts as its kept one, as From and as a stop."""
    merge_e2_into_r3(path=copy)
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
        merged = repo.choices(path=(*T1, (Level.STATION, 'R3')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {'R1', 'R2'}
    assert merged is not None
    assert not any(c.dim for c in merged.items)


def test_to_column_stops_at_a_turn_back(copy: Path) -> None:
    """A run that turns back reaches nothing new after the turn."""
    set_runs(path=copy, runs=[('W2 W1 J C J R1 R2 R3', 4)])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {
        'R1', 'R2', 'R3', 'E1', 'E2',
    }


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


def test_to_column_for_a_station_off_the_line_is_none(copy: Path) -> None:
    """A From not on the line has no TO column; a merged one is its kept."""
    merge_e2_into_r3(path=copy)
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        stale = repo.choices(path=(*T1, (Level.STATION, 'B2')))
        merged = repo.choices(path=(*T1, (Level.STATION, 'E2')))
    assert stale is None
    assert merged is not None
    assert [c.key for c in merged.items if c.tag == 'ALL'] == ['R3']


def test_to_column_cuts_at_a_merged_revisit(copy: Path) -> None:
    """Calling at a merged station counts as calling at its kept one."""
    merge_e2_into_r3(path=copy)
    set_runs(path=copy, runs=[('W2 J R3 C E2 E1', 4)])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {'R2', 'R1', 'E1', 'W1'}


def test_station_column_on_old_timetable_is_none(copy: Path) -> None:
    """A schema-3 file has no station column but keeps Bus and strips."""
    schema_three(path=copy)
    bus: ChooserPath = ((Level.KIND, 'bus'),)
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        assert repo.choices(path=T1) is None
        assert repo.choices(path=(*T1, (Level.STATION, 'W2'))) is None
        assert labels(column=repo.choices(path=())) == ['Bus', 'Train']
        assert repo.choices(path=(*bus, (Level.GROUP, '800s'))) is not None
        assert repo.reach_input(
            key=StripKey(station='C', kind=RouteKind.TRAIN, routes=()),
            origin=place(stop_id='C'),
        ) is not None


BUS: Final[ChooserPath] = ((Level.KIND, 'bus'),)
REPLACEMENTS: Final[ChooserPath] = (
    *BUS, (Level.REPLACEMENTS, 'replacements'),
)
REPLACEMENT_ROWS: Final[tuple[RouteInsert, ...]] = (
    ('replacement:2T1:b', 'replacement', '', 0, '2T1', 2, 'Bee', 'Tg', 'T1'),
    ('replacement:1T1:a', 'replacement', '', 0, '1T1', 1, 'Ay', '', 'T1'),
    ('replacement:1T2:c', 'replacement', '', 0, '1T2', 3, 'Cee', '', 'T2'),
    ('replacement:1BM:d', 'replacement', '', 0, '1BM', 4, 'Dee', '', 'BMT'),
)


def test_bus_groups_end_with_replacement_buses(copy: Path) -> None:
    """Replacement buses close the Bus groups once any route has a line."""
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        before = repo.choices(path=BUS)
    add_routes(path=copy, rows=REPLACEMENT_ROWS)
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        after = repo.choices(path=BUS)
        school = repo.choices(path=((Level.KIND, 'school'),))
    assert before is not None
    assert after is not None
    assert after.items[:-1] == before.items
    assert after.items[-1] == Choice(
        key='replacements', label='Trackwork', rule_above=True,
        level=Level.REPLACEMENTS,
    )
    assert school is None or school.items[-1].key != 'replacements'


def test_replacement_path_lists_lines_then_routes(copy: Path) -> None:
    """Replacement buses list lines, then a line's routes, then towards."""
    add_routes(path=copy, rows=REPLACEMENT_ROWS)
    add_towards(path=copy, route_key='replacement:1T1:a', headsign='W2',
                trips=4)
    lines = (*REPLACEMENTS, (Level.LINE, 'T1'))
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        line_column = repo.choices(path=REPLACEMENTS)
        routes = repo.choices(path=lines)
        towards = repo.choices(
            path=(*lines, (Level.ROUTE, 'replacement:1T1:a')),
        )
    assert line_column is not None
    assert line_column.level is Level.LINE
    assert [(c.key, c.badge) for c in line_column.items] == [
        ('T1', 'T1'), ('T2', 'T2'), ('BMT', 'BMT'),
    ]
    assert line_column.items[0].label == 'Line T1'
    assert routes == Column(level=Level.ROUTE, items=(
        Choice(key='replacement:1T1:a', label='Ay', badge='1T1'),
        Choice(key='replacement:2T1:b', label='Bee', badge='2T1', tag='Tg'),
    ))
    assert towards is not None
    assert [(c.label, c.detail) for c in towards.items] == [('W2', '4')]


def test_train_line_has_no_replacement_row(copy: Path) -> None:
    """A train line's column has no Replacement buses row."""
    add_routes(path=copy, rows=REPLACEMENT_ROWS)
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        assert repo.choices(path=(*T1, (Level.REPLACEMENTS, 'x'))) is None
        column = repo.choices(path=TRAIN)
        stations = repo.choices(path=T1)
    for found in (column, stations):
        assert found is not None
        assert all(c.level is not Level.REPLACEMENTS for c in found.items)


@pytest.mark.parametrize('path', [
    ((Level.KIND, 'tram'),),
    ((Level.KIND, 'bus'), (Level.GROUP, '900s')),
    (*TRAIN, (Level.LINE, 'train:T9')),
    (*T1, (Level.TOWARDS, '4')),
    ((Level.GROUP, '800s'),),
])
def test_unknown_path_answers_none(
    network: SqliteNetworkRepository, path: ChooserPath,
) -> None:
    """A path the catalogue doesn't know has no next column."""
    assert network.choices(path=path) is None


def test_towards_line_has_names_and_points(network_db: Path) -> None:
    """A bus towards line lists its stations with names and points."""
    with closing(sqlite3.connect(network_db)) as conn:
        (towards,) = conn.execute(
            "SELECT towards_id FROM chooser_towards "
            "WHERE route_key = 'bus:811:Line 811'",
        ).fetchone()
    with (
        closing(SqliteNetworkRepository(path=network_db)) as repo,
        closing(SqliteTimetableRepository(path=network_db)) as timetable,
    ):
        line = repo.towards_line(towards=towards)
        expected = tuple(
            timetable.stop(stop_id=station)
            for station in ('B2', 'B3')
        )
        unknown = repo.towards_line(towards=999)
    assert line == expected
    assert all(stop.point is not None for stop in expected)
    assert unknown is None


def test_reach_input_for_train_takes_every_train_run(
    network_db: Path,
) -> None:
    """A Train strip takes the runs of every train route."""
    tails = tails_from(path=network_db, station='C')
    assert sum(tail.trips for tail in tails) == 75
    assert ('L1', 'L2', 'J') in {tail.after for tail in tails}


def test_reach_input_for_bus_filters_by_route(network_db: Path) -> None:
    """A Bus strip takes only its own route's runs."""
    tails = tails_from(
        path=network_db, station='B2', kind=RouteKind.BUS, routes=('811',),
    )
    assert tails == (RunTail(before=(), after=('B3',), trips=3),)


def test_reach_input_for_bus_with_no_routes_takes_every_bus_route(
    network_db: Path,
) -> None:
    """A Bus strip with no routes chosen takes every bus route's runs."""
    tails = tails_from(path=network_db, station='B2', kind=RouteKind.BUS)
    assert set(tails) == {
        RunTail(before=(), after=('B3',), trips=3),
        RunTail(before=('B1',), after=(), trips=3),
    }


def test_reach_input_with_no_kind_takes_every_run(network_db: Path) -> None:
    """A strip with no kind takes buses and trains alike."""
    tails = tails_from(path=network_db, station='B2', kind=None)
    assert {tail.after for tail in tails} == {('B3',), ()}


def test_reach_input_maps_a_platform_to_its_station(
    network: SqliteNetworkRepository,
) -> None:
    """A platform From reads the runs of its station."""
    found = network.reach_input(
        key=StripKey(station='W1_1', kind=RouteKind.TRAIN, routes=()),
        origin=place(stop_id='W1_1'),
    )
    assert found is not None
    assert found.origin.stop_id == 'W1'
    assert found.origin.name == 'W1 Stop'
    assert found.station_of['W1_1'] == 'W1'
    assert found.station_of['W1'] == 'W1'
    assert found.station_of['E2_1'] == 'E2'
    assert found.places['E2'].name == 'E2 Stop'
    assert found.tails


def test_reach_input_cuts_each_tail_at_the_turn_back(
    network_db: Path,
) -> None:
    """y1 runs on as y2 back to J; its tail stops at W2."""
    tails = tails_from(path=network_db, station='C')
    afters = {tail.after for tail in tails}
    assert ('J', 'W1', 'W2') in afters
    assert all(len(set(after)) == len(after) for after in afters)
    assert all('C' not in after for after in afters)


def test_reach_past_a_city_circle_loop(copy: Path) -> None:
    """A loop run gives a tail per pass, cut before its next revisit."""
    set_runs(path=copy, runs=[('C L1 L2 J C E1 E2', 6)])
    assert tails_from(path=copy, station='J') == (
        RunTail(before=('C', 'L1', 'L2'), after=('C', 'E1', 'E2'), trips=6),
    )
    assert set(tails_from(path=copy, station='C')) == {
        RunTail(before=(), after=('L1', 'L2', 'J'), trips=6),
        RunTail(before=('C', 'L1', 'L2', 'J'), after=('E1', 'E2'), trips=6),
    }


def test_line_strip_cuts_tails_off_the_line(
    network_db: Path, copy: Path,
) -> None:
    """A T1 strip at C cuts each tail before T2's L1 and L2."""
    tails = tails_from(path=network_db, station='C', routes=('T1',))
    assert sum(tail.trips for tail in tails) == 75
    assert not {'L1', 'L2'} & {
        station for tail in tails for station in tail.before + tail.after
    }
    set_runs(path=copy, runs=[('C L1 L2 J C E1 E2', 6)])
    assert set(tails_from(path=copy, station='C', routes=('T1',))) == {
        RunTail(before=(), after=(), trips=6),
        RunTail(before=('J',), after=('E1', 'E2'), trips=6),
    }


def test_line_strip_keeps_express_runs_whole(copy: Path) -> None:
    """Runs skipping stations of the line keep every station they call at."""
    set_runs(path=copy, runs=[('E2 C W1', 4), ('R3 R1 J C E2', 2)])
    assert set(tails_from(path=copy, station='C', routes=('T1',))) == {
        RunTail(before=('E2',), after=('W1',), trips=4),
        RunTail(before=('R3', 'R1', 'J'), after=('E2',), trips=2),
    }


def test_line_strip_cuts_at_the_first_station_off_the_line(
    copy: Path,
) -> None:
    """Each tail stops before its first station off the line, outward."""
    set_runs(path=copy, runs=[('W2 L2 J C E1 L1 E2', 3)])
    assert tails_from(path=copy, station='C', routes=('T1',)) == (
        RunTail(before=('J',), after=('E1',), trips=3),
    )


def test_line_strip_counts_an_alias_as_its_kept_station(copy: Path) -> None:
    """A station merged into one on the line counts as on the line."""
    merge_e2_into_r3(path=copy)
    set_runs(path=copy, runs=[('W1 J C E1 E2', 3)])
    assert tails_from(path=copy, station='C', routes=('T1',)) == (
        RunTail(before=('W1', 'J'), after=('E1', 'E2'), trips=3),
    )


def test_line_strip_without_line_stations_is_uncut(copy: Path) -> None:
    """A schema-3 file leaves a T1 strip's tails whole."""
    schema_three(path=copy)
    tails = tails_from(path=copy, station='C', routes=('T1',))
    assert ('L1', 'L2', 'J') in {tail.after for tail in tails}
    set_runs(path=copy, runs=[('C L1 L2 J C E1 E2', 6)])
    assert set(tails_from(path=copy, station='C', routes=('T1',))) == {
        RunTail(before=(), after=('L1', 'L2', 'J'), trips=6),
        RunTail(before=('C', 'L1', 'L2', 'J'), after=('E1', 'E2'), trips=6),
    }


def line_input(*, path: Path, station: str, line: str) -> ReachInput:
    """
    Read a Train line strip's reach input.

    Parameters
    ----------
    path : Path
        Timetable file.
    station : str
        From station ID.
    line : str
        Line number, such as 'T1'.

    Returns
    -------
    ReachInput
        The reach input.
    """
    with closing(SqliteNetworkRepository(path=path)) as repo:
        found = repo.reach_input(
            key=StripKey(station=station, kind=RouteKind.TRAIN,
                         routes=(line,)),
            origin=place(stop_id=station),
        )
    assert found is not None
    return found


def test_t1_strip_at_c_has_an_exit_to_l1(network_db: Path) -> None:
    """T2 trains leave T1's line at C for L1."""
    found = line_input(path=network_db, station='C', line='T1')
    (way,) = found.exits
    assert way == LineExit(
        junction='C', off='L1', lines=('T2',), trips=way.trips,
    )
    assert way.trips > 0
    assert found.places['L1'].name == 'L1 Stop'
    assert found.station_of['L1_1'] == 'L1'


def test_t2_strip_at_c_hangs_two_exits_at_j(network_db: Path) -> None:
    """J's exits to W1 and R1, busier first, sit beyond it, named."""
    reach = build_reach(data=line_input(
        path=network_db, station='C', line='T2',
    ))
    keys = [row.place.stop_id for row in reach.rows]
    junction = keys.index('J')
    out = -1 if reach.rows[junction].above else 1
    beyond = [reach.rows[junction + out * step] for step in (1, 2)]
    assert [row.place.stop_id for row in beyond] == ['W1', 'R1']
    assert {row.kind for row in beyond} == {RowKind.EXIT}
    rows = build_strip_view(inputs=inputs_of(reach=reach)).rows
    names = {row.station: row.name for row in rows}
    assert (names['W1'], names['R1']) == ('W1 Stop · T1', 'R1 Stop · T1')


def test_pass_through_station_stays_on_the_strip(tmp_path: Path) -> None:
    """T1 runs through R2 without stopping: not cut there, no exit."""
    files = network_files()
    files['stop_times.txt'] = [
        {**row, 'pickup_type': '1', 'drop_off_type': '1'}
        if row['stop_id'] == 'R2_1' else row
        for row in files['stop_times.txt']
    ]
    out = tmp_path / 'timetable.db'
    build_timetable(
        sources=[Source(
            name='sydneytrains',
            archive=write_feed(path=tmp_path / 'n.zip', files=files),
        )],
        window=Window(start=date(2026, 10, 5), days=3),
        out=out,
    )
    found = line_input(path=out, station='C', line='T1')
    assert ('J', 'R1', 'R2', 'R3') in {tail.after for tail in found.tails}
    assert 'R2' not in {each.off for each in found.exits}


def test_line_strip_without_line_track_cuts_at_line_stations(
    copy: Path,
) -> None:
    """A file without line_track counts only line_stations as the line."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute("DELETE FROM line_stations WHERE station_id = 'R2'")
        conn.execute(
            "INSERT OR IGNORE INTO line_track VALUES ('train:T1', 'R2')",
        )
        conn.commit()
    tracked = line_input(path=copy, station='C', line='T1')
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE line_track')
        conn.commit()
    listed = line_input(path=copy, station='C', line='T1')
    assert ('J', 'R1', 'R2', 'R3') in {t.after for t in tracked.tails}
    assert ('J', 'R1') in {t.after for t in listed.tails}


def test_exits_need_line_tables(copy: Path) -> None:
    """A schema-3 file gives no exits and leaves the tails whole."""
    schema_three(path=copy)
    found = line_input(path=copy, station='C', line='T1')
    assert found.exits == ()
    assert ('L1', 'L2', 'J') in {tail.after for tail in found.tails}


def test_exits_without_their_table(copy: Path) -> None:
    """A file with line stations but no line_exits still cuts, no exits."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE line_exits')
        conn.commit()
    found = line_input(path=copy, station='C', line='T1')
    assert found.exits == ()
    assert 'L1' not in {
        key for tail in found.tails for key in tail.before + tail.after
    }


def test_line_strip_merges_tails_made_alike(copy: Path) -> None:
    """Tails the cut makes alike merge, keeping the heavier one's before."""
    set_runs(path=copy, runs=[('J C E1 L1', 2), ('W1 J C E1', 5)])
    assert tails_from(path=copy, station='C', routes=('T1',)) == (
        RunTail(before=('W1', 'J'), after=('E1',), trips=7),
    )


def test_reach_input_merges_identical_tails(copy: Path) -> None:
    """Runs with the same tail merge, keeping the heavier run's before."""
    set_runs(path=copy, runs=[('E2 E1 C J W1', 2), ('E1 C J W1', 5)])
    assert tails_from(path=copy, station='C') == (
        RunTail(before=('E1',), after=('J', 'W1'), trips=7),
    )


def test_reach_input_merge_tie_keeps_the_first_run(copy: Path) -> None:
    """Merging runs of equal trips keeps the first run's before."""
    set_runs(path=copy, runs=[('E2 E1 C J W1', 3), ('E1 C J W1', 3)])
    assert tails_from(path=copy, station='C') == (
        RunTail(before=('E2', 'E1'), after=('J', 'W1'), trips=6),
    )


@pytest.mark.skipif(
    not has_runs(path=REAL_DB),
    reason='needs data/timetable.db with run tables',
)
def test_town_hall_train_strip_reaches_the_inner_west() -> None:
    """Town Hall's Train strip reaches Lewisham, Liverpool, Leppington."""
    with closing(SqliteNetworkRepository(path=REAL_DB)) as repo:
        found = repo.reach_input(
            key=StripKey(station=TOWN_HALL, kind=RouteKind.TRAIN, routes=()),
            origin=place(stop_id=TOWN_HALL),
        )
    assert found is not None
    reached = {
        found.places[station].name
        for tail in found.tails
        for station in tail.after
    }
    assert {
        'Lewisham Station', 'Liverpool Station', 'Leppington Station',
    } <= reached


def test_old_schema_answers_none(copy: Path) -> None:
    """A schema-2 file has no columns, lines or runs."""
    old_schema(path=copy)
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        assert repo.choices(path=()) is None
        assert repo.choices(path=T1) is None
        assert repo.towards_line(towards=1) is None
        assert repo.reach_input(
            key=StripKey(station='C', kind=None, routes=()),
            origin=place(stop_id='C'),
        ) is None


def test_reopens_after_replace(copy: Path, tmp_path: Path) -> None:
    """A schema-3 file replacing a schema-2 one is read at once."""
    old = tmp_path / 'old.db'
    shutil.copy(copy, old)
    old_schema(path=old)
    live = tmp_path / 'live.db'
    os.replace(old, live)
    with closing(SqliteNetworkRepository(path=live)) as repo:
        before = repo.choices(path=())
        os.replace(copy, live)
        after = repo.choices(path=())
    assert before is None
    assert labels(column=after) == ['Bus', 'Train']


def test_injected_connection_is_read(network_db: Path) -> None:
    """A repository given a connection reads it, through the port."""
    conn = sqlite3.connect(network_db, check_same_thread=False)
    with closing(SqliteNetworkRepository(connection=conn)) as repo:
        port: NetworkRepository = repo
        assert labels(column=port.choices(path=())) == ['Bus', 'Train']


def test_train_lines_rule_off_each_group(copy: Path) -> None:
    """A rule sits above the first intercity and the first TrainLink line."""
    add_routes(path=copy, rows=[
        ('train:BMT', 'train', '', 0, 'BMT', 101, 'Blue Mountains', '', ''),
        ('train:CCN', 'train', '', 0, 'CCN', 102, 'Central Coast', '', ''),
        ('train:NRC', 'train', '', 0, 'NRC', 111, 'North Coast', '', ''),
        ('train:WST', 'train', '', 0, 'WST', 114, 'Western', '', ''),
    ])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        lines = repo.choices(path=TRAIN)
    assert lines is not None
    assert [(c.badge, c.rule_above) for c in lines.items] == [
        ('T1', False), ('T2', False), ('BMT', True), ('CCN', False),
        ('NRC', True), ('WST', False),
    ]


def test_ferry_lines_rule_off_the_non_sydney_ferries(copy: Path) -> None:
    """Ferry opens a LINE column; a rule sits above the first non-F row."""
    add_routes(path=copy, rows=[
        ('ferry:F1', 'ferry', '', 0, 'F1', 1, 'Manly', '', ''),
        ('ferry:F2', 'ferry', '', 0, 'F2', 2, 'Taronga', '', ''),
        ('ferry:SM', 'ferry', '', 0, 'SM', 200, 'Stockton', '', ''),
    ])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        ferries = repo.choices(path=((Level.KIND, 'ferry'),))
    assert ferries is not None
    assert ferries.level is Level.LINE
    assert [(c.badge, c.rule_above) for c in ferries.items] == [
        ('F1', False), ('F2', False), ('SM', True),
    ]


def test_coach_opens_a_route_column(copy: Path) -> None:
    """Coach lists routes, not lines."""
    add_routes(path=copy, rows=[
        ('coach:C1:x', 'coach', '', 0, 'C1', 1, 'Far', '', ''),
    ])
    with closing(SqliteNetworkRepository(path=copy)) as repo:
        coaches = repo.choices(path=((Level.KIND, 'coach'),))
    assert coaches == Column(level=Level.ROUTE, items=(
        Choice(key='coach:C1:x', label='Far', badge='C1'),
    ))
