"""Tests for network db: to column."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SydneyMode
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.chooser import Choice, ChooserPath, Column, Level
from paperroute_transit.core.reach import StripKey
from tests.adapters.network_db.helpers import (
    LINE_CITY,
    T1,
    TRAIN,
    RouteInsert,
    add_routes,
    add_towards,
    labels,
    merge_e2_into_r3,
    opened,
    place,
    set_runs,
)
from tests.helpers import schema_four, schema_three


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
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
        merged = repo.choices(path=(*T1, (Level.STATION, 'R3')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {'R1', 'R2'}
    assert merged is not None
    assert not any(c.dim for c in merged.items)


def test_to_column_stops_at_a_turn_back(copy: Path) -> None:
    """A run that turns back reaches nothing new after the turn."""
    set_runs(path=copy, runs=[('W2 W1 J C J R1 R2 R3', 4)])
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {
        'R1', 'R2', 'R3', 'E1', 'E2',
    }


def test_to_column_dims_a_station_runs_pass_through(copy: Path) -> None:
    """A run passing C without stopping leaves C grey from W2."""
    set_runs(path=copy, runs=[('W2 W1 J ~C E1 E2', 4)])
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {
        'R1', 'R2', 'R3', 'C',
    }


def test_to_column_from_a_pass_through_reaches_nothing(copy: Path) -> None:
    """From C, which the only run passes without stopping, all is grey."""
    set_runs(path=copy, runs=[('W2 W1 J ~C E1 E2', 4)])
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'C')))
    assert column is not None
    assert {c.key for c in column.items if not c.dim} == {'', 'C'}


def test_to_column_keeps_one_trip_stopping_patterns_apart(
    copy: Path,
) -> None:
    """Two trips of one run stopping at C or at E1 never join C to E1."""
    set_runs(path=copy, runs=[('W2 W1 J C ~E1 E2', 4)])
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute(
            "INSERT INTO run_calls VALUES (1, ?, '[]', '[]')",
            (json.dumps(['W2', 'W1', 'J', 'E1', 'E2']),),
        )
        conn.commit()
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'C')))
    assert column is not None
    assert {c.key for c in column.items if not c.dim} == {'', 'C', 'E2'}


def test_to_column_without_run_calls_counts_passes(copy: Path) -> None:
    """A file without run_calls counts a pass-through as reached."""
    set_runs(path=copy, runs=[('W2 W1 J ~C E1 E2', 4)])
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE run_calls')
        conn.commit()
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {'R1', 'R2', 'R3'}


def test_to_column_for_a_station_off_the_line_is_none(copy: Path) -> None:
    """A From not on the line has no TO column; a merged one is its kept."""
    merge_e2_into_r3(path=copy)
    with opened(path=copy) as repo:
        stale = repo.choices(path=(*T1, (Level.STATION, 'B2')))
        merged = repo.choices(path=(*T1, (Level.STATION, 'E2')))
    assert stale is None
    assert merged is not None
    assert [c.key for c in merged.items if c.tag == 'ALL'] == ['R3']


def test_to_column_cuts_at_a_merged_revisit(copy: Path) -> None:
    """Calling at a merged station counts as calling at its kept one."""
    merge_e2_into_r3(path=copy)
    set_runs(path=copy, runs=[('W2 J R3 C E2 E1', 4)])
    with opened(path=copy) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, 'W2')))
    assert column is not None
    assert {c.key for c in column.items if c.dim} == {'R2', 'R1', 'E1', 'W1'}


def test_station_column_on_old_timetable_is_none(copy: Path) -> None:
    """A schema-3 file has no station column but keeps Bus and strips."""
    schema_three(path=copy)
    bus: ChooserPath = ((Level.KIND, 'bus'),)
    with opened(path=copy) as repo:
        assert repo.choices(path=T1) is None
        assert repo.choices(path=(*T1, (Level.STATION, 'W2'))) is None
        assert labels(column=repo.choices(path=())) == ['Bus', 'Train']
        assert repo.choices(path=(*bus, (Level.GROUP, '800s'))) is not None
        assert repo.reach_input(
            key=StripKey(station='C', kind=SydneyMode.TRAIN, routes=()),
            origin=place(stop_id='C'),
        ) is not None


def test_schema_four_file_serves_chooser_and_strip(copy: Path) -> None:
    """A schema-4 file still answers the chooser's and strip's queries."""
    schema_four(path=copy)
    with opened(path=copy) as repo:
        assert labels(column=repo.choices(path=())) == ['Bus', 'Train']
        assert repo.choices(path=T1) is not None
        assert repo.towards_line(towards=1) is not None
        assert repo.reach_input(
            key=StripKey(station='C', kind=SydneyMode.TRAIN, routes=()),
            origin=place(stop_id='C'),
        ) is not None


BUS: Final[ChooserPath] = ((Level.KIND, 'bus'),)


REPLACEMENTS: Final[ChooserPath] = (
    *BUS, (Level.REPLACEMENTS, 'replacement'),
)


REPLACEMENT_ROWS: Final[tuple[RouteInsert, ...]] = (
    ('replacement:2T1:b', 'replacement', '', 0, '2T1', 2, 'Bee', 'Tg', 'T1'),
    ('replacement:1T1:a', 'replacement', '', 0, '1T1', 1, 'Ay', '', 'T1'),
    ('replacement:1T2:c', 'replacement', '', 0, '1T2', 3, 'Cee', '', 'T2'),
    ('replacement:1BM:d', 'replacement', '', 0, '1BM', 4, 'Dee', '', 'BMT'),
)


def test_bus_groups_end_with_replacement_buses(copy: Path) -> None:
    """Replacement buses close the Bus groups once any route has a line."""
    with opened(path=copy) as repo:
        before = repo.choices(path=BUS)
    add_routes(path=copy, rows=REPLACEMENT_ROWS)
    with opened(path=copy) as repo:
        after = repo.choices(path=BUS)
        school = repo.choices(path=((Level.KIND, 'school'),))
    assert before is not None
    assert after is not None
    assert after.items[:-1] == before.items
    assert after.items[-1] == Choice(
        key='replacement', label='Trackwork', rule_above=True,
        level=Level.REPLACEMENTS,
    )
    assert school is None or school.items[-1].key != 'replacement'


def test_replacement_path_lists_lines_then_routes(copy: Path) -> None:
    """Replacement buses list lines, then a line's routes, then towards."""
    add_routes(path=copy, rows=REPLACEMENT_ROWS)
    add_towards(path=copy, route_key='replacement:1T1:a', headsign='W2',
                trips=4)
    lines = (*REPLACEMENTS, (Level.LINE, 'T1'))
    with opened(path=copy) as repo:
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
    with opened(path=copy) as repo:
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
        opened(path=network_db) as repo,
        closing(
            SqliteTimetableRepository(
                path=network_db,
                city=LINE_CITY,
            ),
        ) as timetable,
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
