"""Tests for building the run tables in a child process."""

import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute.build import extras, timetable
from paperroute.build.extras import build_extras
from paperroute.build.handoff import HandoffRecord, write_handoff
from paperroute.build.timetable import Window, build_timetable
from paperroute.city import SYDNEY
from paperroute_transit.build.gtfs_files import Source
from tests.build.network_feed import network_feed

RUN_TABLES: Final[set[str]] = {
    'runs', 'run_stops', 'run_calls', 'trip_runs', 'trip_next',
    'trip_joins', 'route_patterns', 'chooser_routes', 'chooser_towards',
}
IMPORTS_ONLY: Final[str] = (
    'import paperroute.build.extras, sys; '
    "assert not {'numpy', 'aiohttp'} & set(sys.modules)"
)
CHILD_RUN: Final[str] = (
    'import runpy, sys\n'
    "sys.argv = ['extras', *sys.argv[1:]]\n"
    'try:\n'
    "    runpy.run_module('paperroute.build.extras', run_name='__main__')\n"
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
    out = folder / 'timetable.db'
    source = Source(name='sydneytrains',
                    archive=network_feed(path=folder / 'network.zip'))
    build_timetable(city=SYDNEY, sources=[source],
                    window=Window(start=date(2026, 10, 5), days=3), out=out)
    return out


def skip_extras(*, path: Path) -> None:
    """
    Stand in for build_extras and build nothing.

    Parameters
    ----------
    path : Path
        Timetable file, ignored.
    """
    del path


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


def test_run_extras_prints_each_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each phase prints its start, its time and the child's peak."""
    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    db = network_build(folder=tmp_path)
    assert not RUN_TABLES & tables_of(db=db)
    capsys.readouterr()
    assert extras.main(argv=[str(db)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == 'progress runs'
    assert lines[1].startswith('progress runs done ')
    assert lines[1].endswith('s')
    assert lines[2] == 'progress catalogue'
    assert lines[3].startswith('progress catalogue done ')
    assert lines[-1].startswith('progress extras peak ')
    assert lines[-1].endswith('MB')
    assert RUN_TABLES <= tables_of(db=db)


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
        build_extras(path=junk)


def test_extras_never_import_numpy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extras module and its real child run load neither numpy nor aiohttp."""
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
        [str(db)],
        [str(record.scratch), str(handed / 'handoff.json')],
    ):
        child = subprocess.run(
            [sys.executable, '-c', CHILD_RUN, *argv],
            check=False, capture_output=True, text=True,
        )
        assert child.returncode == 0, child.stderr
    assert RUN_TABLES <= tables_of(db=db)
    assert RUN_TABLES <= tables_of(db=record.out)
