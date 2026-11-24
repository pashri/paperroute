"""Tests for network db: strips."""

import os
import shutil
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_REF, SydneyMode
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.factories import inputs_of
from paperroute_transit.adapters.network_db import (
    SqliteNetworkRepository,
    route_choices,
)
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.chooser import Choice, Column, Level
from paperroute_transit.core.reach import (
    ReachInput,
    RowKind,
    RunTail,
    StripKey,
    build_reach,
)
from paperroute_transit.core.reach_layout import LineExit
from paperroute_transit.core.strip_view import build_strip_view
from paperroute_transit.ports import NetworkRepository
from tests.adapters.network_db.helpers import (
    LINE_CITY,
    T1,
    TRAIN,
    add_named_lines,
    add_routes,
    labels,
    opened,
    place,
    set_runs,
    tails_from,
)
from tests.build.network_feed import pass_through_files
from tests.helpers import has_runs, old_schema, schema_three

REAL_DB: Final[Path] = Path(__file__).parents[3] / 'data' / 'timetable.db'


TOWN_HALL: Final[str] = '200070'


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
    with opened(path=path) as repo:
        found = repo.reach_input(
            key=StripKey(station=station, kind=SydneyMode.TRAIN,
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
    files = pass_through_files()
    out = tmp_path / 'timetable.db'
    build_timetable(
        sources=[Source(
            name='sydneytrains',
            archive=write_feed(path=tmp_path / 'n.zip', files=files),
        )],
        window=Window(start=date(2026, 10, 5), days=3),
        out=out,
        city_ref=SYDNEY_REF,
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
    with opened(path=REAL_DB) as repo:
        found = repo.reach_input(
            key=StripKey(station=TOWN_HALL, kind=SydneyMode.TRAIN, routes=()),
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
    with opened(path=copy) as repo:
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
    with opened(path=live) as repo:
        before = repo.choices(path=())
        os.replace(copy, live)
        after = repo.choices(path=())
    assert before is None
    assert labels(column=after) == ['Bus', 'Train']


def test_injected_connection_is_read(network_db: Path) -> None:
    """A repository given a connection reads it, through the port."""
    conn = sqlite3.connect(network_db, check_same_thread=False)
    with closing(SqliteNetworkRepository(
        connection=conn,
        city=LINE_CITY,
    )) as repo:
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
    with opened(path=copy) as repo:
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
    with opened(path=copy) as repo:
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
    with opened(path=copy) as repo:
        coaches = repo.choices(path=((Level.KIND, 'coach'),))
    assert coaches == Column(level=Level.ROUTE, items=(
        Choice(key='coach:C1:x', label='Far', badge='C1'),
    ))


def test_route_choices_carry_a_named_line_value() -> None:
    """A line keyed by long name keeps its value; others leave it empty."""
    rows = [
        ('train:GA:Norwich', 'GA', 'Norwich', ''),
        ('train:T1', 'T1', 'North Shore & Western', ''),
    ]
    assert [
        (c.badge, c.value) for c in route_choices(rows=rows, line_kind='train')
    ] == [('GA', 'GA:Norwich'), ('T1', '')]
    assert {c.value for c in route_choices(rows=rows)} == {''}


def test_line_column_carries_a_named_line_value(copy: Path) -> None:
    """A train line keyed by long name has that name as its value."""
    add_named_lines(path=copy)
    with opened(path=copy) as repo:
        lines = repo.choices(path=TRAIN)
    assert lines is not None
    assert [(c.badge, c.value) for c in lines.items] == [
        ('T1', ''), ('T2', ''), ('GA', 'GA:Norwich'), ('GA', 'GA:Liverpool'),
    ]
