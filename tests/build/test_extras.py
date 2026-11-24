"""Tests for building the run tables in a child process."""

import dataclasses
import os
import shutil
import sqlite3
import subprocess
import sys
from array import array
from collections.abc import Collection
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY, SYDNEY_REF
from paperroute_transit.build import extras, timetable
from paperroute_transit.build.extras import build_extras
from paperroute_transit.build.handoff import (
    Handoff,
    HandoffRecord,
    hand_over,
    write_handoff,
)
from paperroute_transit.build.runs import RunReport, load_runs
from paperroute_transit.build.track_db import RailTrack, write_track
from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.modes import Classifier
from tests.build.network_feed import LAT, LON, STEP_LON, build_network_db

RUN_TABLES: Final[set[str]] = {
    'runs', 'run_stops', 'run_calls', 'trip_runs', 'trip_next',
    'trip_joins', 'route_patterns', 'chooser_routes', 'chooser_towards',
    'rail_hops', 'track_places', 'weekday_runs', 'stop_pairs',
    'parent_kerbs', 'trip_turns',
}
IMPORTS_ONLY: Final[str] = (
    'import paperroute_transit.build.extras, sys; '
    "assert not {'numpy', 'aiohttp'} & set(sys.modules)"
)
CHILD_RUN: Final[str] = (
    'import runpy, sys\n'
    "sys.argv = ['extras', *sys.argv[1:]]\n"
    'try:\n'
    '    runpy.run_module(\n'
    "        'paperroute_transit.build.extras', run_name='__main__'\n"
    '    )\n'
    'except SystemExit as stop:\n'
    '    assert stop.code == 0, stop.code\n'
    'else:\n'
    "    raise AssertionError('extras did not exit')\n"
    "loaded = {'numpy', 'aiohttp'} & set(sys.modules)\n"
    'assert not loaded, loaded\n'
)


def network_build(*, folder: Path) -> Path:
    """
    Build the network feed into a 3-day timetable.

    Parameters
    ----------
    folder : Path
        Directory for the feed and the timetable.

    Returns
    -------
    Path
        Timetable file.
    """
    return build_network_db(folder=folder)


def skip_extras(*, path: Path, city_ref: str, tiles: Path | None) -> None:
    """
    Stand in for build_extras and build nothing.

    Parameters
    ----------
    path : Path
        Timetable file, ignored.
    city_ref : str
        The city's reference, ignored.
    tiles : Path | None
        The tiles file, ignored.
    """
    del path, city_ref, tiles


def tables_of(*, db: Path) -> set[str]:
    """
    List the tables of a database.

    Parameters
    ----------
    db : Path
        Database file.

    Returns
    -------
    set[str]
        Table names.
    """
    with closing(sqlite3.connect(db)) as conn:
        return {
            name
            for name, in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }


def spy_extras(
    *, monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, Path | None, Path | None]]:
    """
    Replace the build and the landing with recorders.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Patches undone after the test.

    Returns
    -------
    list[tuple[str, Path | None, Path | None]]
        Filled by each call: ``built`` with the tiles file, or ``landed``
        with the handoff file.
    """
    calls: list[tuple[str, Path | None, Path | None]] = []

    def build(*, path: Path, city: object, tiles: Path | None) -> None:
        del path, city
        calls.append(('built', tiles, None))

    def land(
        *, scratch: Path, path: Path, build: object, city_ref: str,
    ) -> int:
        del scratch, build, city_ref
        calls.append(('landed', None, path))
        return 0

    monkeypatch.setattr(extras, 'run_extras', build)
    monkeypatch.setattr(extras, 'land', land)
    return calls


def test_main_reads_the_old_handoff_argument_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """City, scratch and handoff, with no tiles, still lands."""
    calls = spy_extras(monkeypatch=monkeypatch)
    handoff = tmp_path / 'handoff.json'
    assert extras.main(
        argv=[SYDNEY_REF, str(tmp_path / 'x.new'), str(handoff)],
    ) == 0
    assert calls == [('landed', None, handoff)]


def test_main_reads_the_new_argument_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiles slot builds as a child; a handoff after it lands."""
    calls = spy_extras(monkeypatch=monkeypatch)
    scratch = str(tmp_path / 'x.new')
    tiles = tmp_path / 'tiles.db'
    assert extras.main(argv=[SYDNEY_REF, scratch, str(tiles)]) == 0
    assert extras.main(argv=[SYDNEY_REF, scratch, '']) == 0
    handoff = tmp_path / 'handoff.json'
    assert extras.main(
        argv=[SYDNEY_REF, scratch, str(tiles), str(handoff)],
    ) == 0
    assert calls == [
        ('built', tiles, None), ('built', None, None),
        ('landed', None, handoff),
    ]


def test_run_extras_prints_each_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each phase prints its start, its time and the child's peak."""
    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    db = network_build(folder=tmp_path)
    assert not RUN_TABLES & tables_of(db=db)
    capsys.readouterr()
    assert extras.main(argv=[SYDNEY_REF, str(db), '']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == 'progress runs'
    assert lines[1].startswith('progress runs done ')
    assert lines[1].endswith('s')
    assert lines[2] == 'progress weekday'
    assert lines[3].startswith('progress weekday done ')
    assert lines[4] == 'progress passed'
    assert lines[5].startswith('progress passed hops=')
    assert ' inside=' in lines[5] and ' added=' in lines[5]
    assert lines[6].startswith('progress passed done ')
    assert lines[7] == 'progress catalogue'
    assert lines[8].startswith('progress catalogue done ')
    assert lines[9] == 'progress pairs'
    assert lines[10].startswith('progress pairs done ')
    assert lines[11] == 'progress parent kerbs'
    assert lines[12].startswith('progress parent kerbs done ')
    assert lines[-1].startswith('progress extras peak ')
    assert lines[-1].endswith('MB')
    assert RUN_TABLES <= tables_of(db=db)


def test_run_extras_survives_a_pairing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pairing error is logged and leaves an empty `stop_pairs`."""

    def broken(**_: object) -> int:
        """Fail as a missing table would."""
        raise sqlite3.OperationalError('no such table: routes')

    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    monkeypatch.setattr(extras, 'load_pairs', broken)
    db = network_build(folder=tmp_path)
    extras.run_extras(path=db, city=SYDNEY, tiles=None)
    assert 'stop_pairs' in tables_of(db=db)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM stop_pairs').fetchone() == (
            0,
        )
    assert 'stop pairing failed' in caplog.text


def test_run_extras_survives_a_parent_kerbs_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A parent kerb error is logged and leaves the table empty."""

    def broken(**_: object) -> int:
        """Fail as a missing table would."""
        raise sqlite3.OperationalError('no such table: routes')

    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    monkeypatch.setattr(extras, 'load_parent_kerbs', broken)
    db = network_build(folder=tmp_path)
    extras.run_extras(path=db, city=SYDNEY, tiles=None)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            'SELECT COUNT(*) FROM parent_kerbs',
        ).fetchone() == (0,)
    assert 'parent kerbs failed' in caplog.text


@pytest.mark.parametrize('turnarounds', [False, True])
def test_run_extras_passes_the_turnarounds_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turnarounds: bool,
) -> None:
    """The run build records turnarounds only for a city that asks."""
    seen: list[bool] = []

    def spy(
        *, conn: sqlite3.Connection, classify: Classifier,
        rail_types: Collection[int], turns: bool,
    ) -> RunReport:
        """Record the flag and build the runs."""
        seen.append(turns)
        return load_runs(
            conn=conn, classify=classify, rail_types=rail_types, turns=turns,
        )

    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    monkeypatch.setattr(extras, 'load_runs', spy)
    db = network_build(folder=tmp_path)
    city = dataclasses.replace(
        SYDNEY,
        timetable=dataclasses.replace(
            SYDNEY.timetable, turnarounds=turnarounds,
        ),
    )
    extras.run_extras(path=db, city=city, tiles=None)
    assert seen == [turnarounds]
    assert 'trip_turns' in tables_of(db=db)


def test_run_parent_kerbs_skips_a_city_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A city without parent kerbs never runs the build."""

    def called(**_: object) -> int:
        """Fail the test if the build runs."""
        raise AssertionError('parent kerbs built')

    monkeypatch.setattr(extras, 'load_parent_kerbs', called)
    city = dataclasses.replace(
        SYDNEY,
        timetable=dataclasses.replace(SYDNEY.timetable, parent_kerbs=False),
    )
    with closing(sqlite3.connect(tmp_path / 't.db')) as conn:
        extras.run_parent_kerbs(conn=conn, city=city)
    assert SYDNEY.timetable.parent_kerbs


def free_pages_after_extras(*, db: Path, compact: bool) -> int:
    """
    Leave free pages in a timetable, run the extras and count them.

    Parameters
    ----------
    db : Path
        Timetable file.
    compact : bool
        Whether the city vacuums its finished timetable.

    Returns
    -------
    int
        Free pages left in the file.
    """
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE ballast (x BLOB)')
        conn.executemany(
            'INSERT INTO ballast VALUES (?)', [(bytes(4096),)] * 200,
        )
        conn.execute('DROP TABLE ballast')
        conn.commit()
    city = dataclasses.replace(
        SYDNEY,
        timetable=dataclasses.replace(SYDNEY.timetable, compact=compact),
    )
    extras.run_extras(path=db, city=city, tiles=None)
    with closing(sqlite3.connect(db)) as conn:
        return int(conn.execute('PRAGMA freelist_count').fetchone()[0])


@pytest.mark.parametrize('compact', [False, True])
def test_run_extras_vacuums_only_a_compact_city(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compact: bool,
) -> None:
    """Only a city that asks for it gets a file with no free pages."""
    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    db = network_build(folder=tmp_path)
    left = free_pages_after_extras(db=db, compact=compact)
    assert (left == 0) is compact


def test_extras_run_in_a_child(
    tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    """The build writes the run tables and both processes' sizes."""
    assert RUN_TABLES <= tables_of(db=network_build(folder=tmp_path))
    lines = capfd.readouterr().out.splitlines()
    parent = lines.index(next(
        line for line in lines if line.startswith('progress extras parent ')
    ))
    assert lines[parent].endswith('MB')
    assert lines[parent + 1] == 'progress runs'
    assert lines[-1].startswith('progress extras peak ')
    junk = tmp_path / 'junk.db'
    junk.write_bytes(b'not a timetable')
    with pytest.raises(RuntimeError, match='exit code'):
        build_extras(path=junk, city_ref=SYDNEY_REF, tiles=None)


def test_extras_never_import_numpy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The extras module and its real child run load neither numpy nor aiohttp.
    """
    imported = subprocess.run(
        [sys.executable, '-c', IMPORTS_ONLY],
        check=False, capture_output=True, text=True,
    )
    assert imported.returncode == 0, imported.stderr
    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    db = network_build(folder=tmp_path)
    handed = tmp_path / 'handed'
    handed.mkdir()
    record = HandoffRecord(
        scratch=handed / 'timetable.new', out=handed / 'timetable.db',
        dropped=0, partial=0, overnight=0, peak_mb=1.0, tail=None,
    )
    shutil.copy(db, record.scratch)
    write_handoff(path=handed / 'handoff.json', record=record)
    for argv in (
        [SYDNEY_REF, str(db), ''],
        [SYDNEY_REF, str(record.scratch), '', str(handed / 'handoff.json')],
    ):
        child = subprocess.run(
            [sys.executable, '-c', CHILD_RUN, *argv],
            check=False, capture_output=True, text=True,
        )
        assert child.returncode == 0, child.stderr
    assert RUN_TABLES <= tables_of(db=db)
    assert RUN_TABLES <= tables_of(db=record.out)


def test_city_named_imports_the_city() -> None:
    """A reference gives its city; one naming anything else is refused."""
    assert extras.city_named(ref=SYDNEY_REF) is SYDNEY
    with pytest.raises(ValueError, match='names no city'):
        extras.city_named(ref='paperroute_sydney.city:SYDNEY_ZONE')


class Execed(Exception):
    """Raised by the stand-in for os.execv."""


def write_line_track(*, path: Path) -> None:
    """
    Store a track along the network feed's east-west line.

    Parameters
    ----------
    path : Path
        Tiles file to create.
    """
    xs, ys = zip(
        *(
            to_metres(
                lon=LON + STEP_LON * step / 10,
                lat=LAT,
                projection=SYDNEY.projection,
            )
            for step in range(60)
        ),
        strict=True,
    )
    edges = [node for i in range(59) for node in (i, i + 1)]
    with closing(sqlite3.connect(path)) as conn:
        write_track(
            conn=conn,
            track=RailTrack(
                xs=array('d', xs),
                ys=array('d', ys),
                edges=array('q', edges),
                stations=(),
            ),
        )
        conn.commit()


def test_a_handed_over_build_reads_the_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tiles path goes through the exec to the hops the child writes."""
    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    db = network_build(folder=tmp_path)
    tiles = tmp_path / 'tiles.db'
    write_line_track(path=tiles)
    record = HandoffRecord(
        scratch=tmp_path / 'timetable.new', out=tmp_path / 'built.db',
        dropped=0, partial=0, overnight=0, peak_mb=1.0, tail=None,
        tiles=str(tiles),
    )
    shutil.copy(db, record.scratch)
    seen: list[list[str]] = []

    def execv(exe: str, argv: list[str]) -> None:
        del exe
        seen.append(argv)
        raise Execed

    monkeypatch.setattr(os, 'execv', execv)
    with pytest.raises(Execed):
        hand_over(
            handoff=Handoff(path=tmp_path / 'handoff.json'),
            record=record,
            city_ref=SYDNEY_REF,
        )
    assert seen[0][-3:-1] == [str(record.scratch), str(tiles)]
    assert extras.main(argv=seen[0][3:]) == 0
    with closing(sqlite3.connect(record.out)) as conn:
        inside = conn.execute(
            "SELECT inside FROM rail_hops WHERE a = 'E2' AND b = 'E1'",
        ).fetchone()
    assert inside == (1,)
