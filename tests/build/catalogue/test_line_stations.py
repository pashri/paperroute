"""Tests for catalogue: line stations."""

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from paperroute_sydney.city import SYDNEY, SYDNEY_REF, SydneyMode
from paperroute_transit.build.catalogue import Entry, kind_rows
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.timetable import Window, build_timetable
from tests.build.network_feed import network_feed


def build_network(*, path: Path) -> Path:
    """
    Build the network feed into a timetable.

    Parameters
    ----------
    path : Path
        Directory for the feed and the timetable.

    Returns
    -------
    Path
        The timetable file.
    """
    out = path / 'timetable.db'
    source = Source(name='sydneytrains',
                    archive=network_feed(path=path / 'network.zip'))
    build_timetable(city_ref=SYDNEY_REF, sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    return out


def test_bus_towards_row_holds_the_network_pattern(tmp_path: Path) -> None:
    """On the network feed, bus 811 has one towards row, B2 to B3."""
    with closing(sqlite3.connect(build_network(path=tmp_path))) as conn:
        trips, stations = conn.execute(
            'SELECT trips, stations FROM chooser_towards '
            "WHERE route_key = 'bus:811:Line 811'",
        ).fetchone()
    assert trips == 1
    assert json.loads(stations) == ['B2', 'B3']


def test_no_towards_rows_for_station_first_lines(tmp_path: Path) -> None:
    """Trains get a line row and stations, but no towards rows."""
    with closing(sqlite3.connect(build_network(path=tmp_path))) as conn:
        keys = {
            key for key, in conn.execute(
                'SELECT DISTINCT route_key FROM chooser_towards',
            )
        }
        lines = conn.execute(
            "SELECT COUNT(*) FROM chooser_routes WHERE kind = 'train'",
        ).fetchone()
    assert keys == {'bus:809:Line 809', 'bus:811:Line 811'}
    assert lines is not None
    assert lines[0] > 0


def test_catalogue_writes_line_stations_for_station_first_lines(
    tmp_path: Path,
) -> None:
    """T1 opens at R3 and splits at J, the busier way to E2 first."""
    out = tmp_path / 'timetable.db'
    source = Source(name='sydneytrains',
                    archive=network_feed(path=tmp_path / 'network.zip'))
    build_timetable(city_ref=SYDNEY_REF, sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    with closing(sqlite3.connect(out)) as conn:
        t1 = conn.execute(
            'SELECT station_id, head FROM line_stations '
            "WHERE line_key = 'train:T1' ORDER BY pos",
        ).fetchall()
        keys = {key for (key,) in conn.execute(
            'SELECT DISTINCT line_key FROM line_stations',
        )}
    assert t1 == [
        ('R3', 'R3'), ('R2', ''), ('R1', ''), ('J', ''), ('C', 'E2'),
        ('E1', ''), ('E2', ''), ('W1', 'W2'), ('W2', ''),
    ]
    assert keys == {'train:T1', 'train:T2'}


def test_catalogue_writes_line_exits(tmp_path: Path) -> None:
    """T1 leaves its line at C for L1 on T2; T2 leaves at J on T1."""
    out = tmp_path / 'timetable.db'
    source = Source(name='sydneytrains',
                    archive=network_feed(path=tmp_path / 'network.zip'))
    build_timetable(city_ref=SYDNEY_REF, sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    with closing(sqlite3.connect(out)) as conn:
        rows = conn.execute(
            'SELECT line_key, station_id, off_id, lines FROM line_exits '
            'ORDER BY line_key, station_id, off_id',
        ).fetchall()
    found: dict[str, list[tuple[str, str, str]]] = {}
    for key, station, off, lines in rows:
        found.setdefault(key, []).append((station, off, lines))
    assert found == {
        'train:T1': [('C', 'L1', 'T2')],
        'train:T2': [('J', 'R1', 'T1'), ('J', 'W1', 'T1')],
    }


def test_train_lines_sort_sydney_then_intercity_then_trainlink() -> None:
    """T1-T9, then the intercity lines, then NRC, NRW, STH and WST."""
    rows = kind_rows(entries=[
        Entry(key=f'train:{code}', kind=SydneyMode.TRAIN, number=code,
              name=code, tag='', line='')
        for code in ('WST', 'XPT', 'HUN', 'NRC', 'T1', 'STH', 'NRW')
    ], modes=SYDNEY.modes, rules=SYDNEY.catalogue)
    assert sorted((row[5], row[4]) for row in rows) == [
        (1, 'T1'), (105, 'HUN'), (111, 'NRC'), (112, 'NRW'), (113, 'STH'),
        (114, 'WST'), (207, 'XPT'),
    ]


def test_ferry_lines_sort_sydney_ferries_first() -> None:
    """F1-F10 come in number order, then the other ferries by name."""
    rows = kind_rows(entries=[
        Entry(key=f'ferry:{code}', kind=SydneyMode.FERRY, number=code,
              name=name, tag='', line='')
        for code, name in (
            ('MFF', 'Zeta'), ('F10', 'Ten'), ('F2', 'Two'), ('SM', 'Alpha'),
        )
    ], modes=SYDNEY.modes, rules=SYDNEY.catalogue)
    assert [row[4] for row in sorted(rows, key=lambda row: row[5])] == [
        'F2', 'F10', 'SM', 'MFF',
    ]
