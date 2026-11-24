"""Tests for the Stop view tick benchmark at a Cambridge stop."""

import importlib.util
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import FIXTURES, RECORDED

SCRIPT = (
    Path(__file__).parent.parent.parent.parent / 'scripts' / 'bench_stop.py'
)


def load_script() -> ModuleType:
    """
    Import the benchmark script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location('bench_stop', SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules['bench_stop'] = module
    spec.loader.exec_module(module)
    return module


def first_stop(*, db: Path) -> str:
    """
    Name a stop the timetable has calls at.

    Parameters
    ----------
    db : Path
        The timetable.

    Returns
    -------
    str
        A stop ID.
    """
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            'SELECT stop_id FROM stop_times GROUP BY stop_id '
            'ORDER BY COUNT(*) DESC LIMIT 1',
        ).fetchone()
    return str(row[0])


def test_bench_stop_runs_at_a_cambridge_stop_with_a_bods_feed(
    built_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The recorded BODS feed is decoded and both modes are timed."""
    argv = [
        '--city', 'cambridge',
        '--db', str(built_path),
        '--stop', first_stop(db=built_path),
        '--at', str(RECORDED['at']),
        '--repeat', '1',
        '--limit', '1000000',
        '--positions', str(FIXTURES / 'bods_positions.pb'),
    ]
    assert load_script().main(argv=argv) == 0
    out = capsys.readouterr().out
    assert re.search(r'^feed decode: median [\d.]+ ms$', out, re.M)
    assert re.search(r'^with gauge: median [\d.]+ ms', out, re.M)
    assert re.search(r'^today: median [\d.]+ ms', out, re.M)
