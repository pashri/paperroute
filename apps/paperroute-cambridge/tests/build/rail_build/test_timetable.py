"""Tests for rail build: timetable."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from paperroute_cambridge.build.__main__ import CAMBRIDGE_BUILD
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from paperroute_transit.build.cli import given_sources, parse_args
from paperroute_transit.build.gtfs_files import Source, read_rows
from paperroute_transit.build.refresh import refresh
from paperroute_transit.build.source_state import load_states
from tests.build.rail_build.helpers import (
    BODS,
    RAIL,
    SUNDAY,
    Shelf,
    built,
    departures,
    trip_counts,
)
from tests.conftest import RECORDED_DAY


def test_the_replacement_bus_joins_its_operators_line(
    tmp_path: Path,
) -> None:
    """The Sunday replacement bus sits on Great Northern's first line."""
    path = built(out=tmp_path / 'timetable.db', start=SUNDAY, days=1)
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=path),
    ) as network:
        lines = network.line_column(kind='train')
    assert lines is not None
    assert [(c.key, c.badge) for c in lines.items] == [
        ('train:GN:Kings Lynn & Kings Cross', 'GN'),
    ]
    assert departures(path=path, day=SUNDAY) == [
        ('06:10', 'GN', '', 'Royston', 'train', 'train'),
    ]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute(
            "SELECT type FROM routes WHERE route_id LIKE 'rail:GN_RRB:%'",
        ).fetchall() == [(714,)]


def test_the_rail_end_does_not_shorten_the_bus_timetable(
    tmp_path: Path,
) -> None:
    """The rail calendar ends 11 Dec; the timetable still runs to its end."""
    path = built(out=tmp_path / 'timetable.db', start=RECORDED_DAY, days=30)
    with closing(SqliteTimetableRepository(city=CAMBRIDGE, path=path)) as repo:
        assert repo.last_day() == date(2026, 12, 15)
        assert repo.source_dates() == {
            'bods': date(2026, 11, 16), 'rail': date(2026, 11, 16),
        }


def test_buses_keep_their_timetable_whatever_the_rail_source_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rail builds buses alone; rail adds trains; rail gone keeps them."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    monkeypatch.setattr(
        refresh_module, 'yesterday', lambda *, zone: RECORDED_DAY,
    )
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    parts = replace(CAMBRIDGE_BUILD, downloader=shelf)
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    reports, counts, stored = [], [], []
    for gone in ({'rail'}, set(), {'rail'}):
        shelf.gone = gone
        reports.append(refresh(
            data_dir=data_dir, city=CAMBRIDGE, parts=parts, days=1, force=True,
        ))
        counts.append(trip_counts(data_dir=data_dir))
        stored.append(load_states(path=data_dir / 'sources' / 'sources.json'))
    assert [(r.failed, r.all_failed, r.rebuilt) for r in reports] == [
        (('rail',), False, True), ((), False, True), (('rail',), False, True),
    ]
    assert reports[1].changed == ('rail',)
    assert 'rail' not in stored[0] and stored[2]['rail'] == stored[1]['rail']
    assert counts == [
        {'bods': 67}, {'bods': 67, 'rail': 5}, {'bods': 67, 'rail': 5},
    ]
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        ends = conn.execute(
            "SELECT key FROM meta WHERE key LIKE 'source_end:%'",
        ).fetchall()
    assert ends == [('source_end:bods',)]


def test_the_build_command_cuts_the_rail_zip_it_is_given(
    tmp_path: Path,
) -> None:
    """The gtfs command cuts the national zip beside its output."""
    out = tmp_path / 'timetable.db'
    args = parse_args(city=CAMBRIDGE, parts=CAMBRIDGE_BUILD, argv=[
        'gtfs', '--bods', str(BODS), '--rail', str(RAIL), '--out', str(out),
    ])
    sources = given_sources(args=args, parts=CAMBRIDGE_BUILD)
    assert sources == [
        Source(name='bods', archive=BODS),
        Source(
            name='rail',
            archive=tmp_path / 'timetable.rail.zip',
            optional=True,
        ),
    ]
    stops = read_rows(archive=sources[1].archive, member='stops.txt')
    assert 'rail:CBG' in {row['stop_id'] for row in stops}
