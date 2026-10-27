"""Tests for the any-route timing benchmark script."""

import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest

from paperroute.core.model import SYDNEY
from tests.helpers import slice_db

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'bench_open_legs.py'
LINE = re.compile(
    r'^.+ -> .+: \d+ rows, median \d+\.\d ms, max \d+\.\d ms$',
)

CHAINS = re.compile(
    r'^.+ -> .+ chains: \d+ chains, median \d+\.\d ms, max \d+\.\d ms$',
)


def load_script() -> ModuleType:
    """
    Import the benchmark script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location('bench_open_legs', SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules['bench_open_legs'] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name='db', scope='module')
def _db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    The real GTFS slice as a database.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the working directory.

    Returns
    -------
    Path
        The database file.
    """
    return slice_db(tmp_path=tmp_path_factory.mktemp('bench'))


def test_bench_prints_a_line_per_pair(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Each pair prints one line in the documented format."""
    status = load_script().main([
        '--db', str(db),
        '--repeat', '1',
        '--at', '2026-10-16T07:00:00+11:00',
        '--pairs', '2145615:215254', 'Hollis St Opp Constitution Hill Library:215020',
    ])
    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert len(lines) == 2
    assert all(LINE.match(line) for line in lines)
    assert lines[0].startswith(
        'Hollis St Opp Constitution Hill Library -> Hammers Rd Opp Sherwood St: ',
    )


def test_bench_unknown_stop_fails(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown stop is reported and the exit code is 1."""
    status = load_script().main([
        '--db', str(db), '--repeat', '1', '--pairs', 'nowhere:215254',
    ])
    assert status == 1
    assert 'unknown stop in nowhere:215254' in capsys.readouterr().err


def test_bench_missing_database_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing database file is reported and the exit code is 1."""
    status = load_script().main(['--db', str(tmp_path / 'none.db')])
    assert status == 1
    assert 'no timetable at' in capsys.readouterr().err


def test_bench_times_chains(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`--chains` times the chain search over each way given."""
    status = load_script().main([
        '--db', str(db),
        '--repeat', '1',
        '--at', '2026-10-16T07:00:00+11:00',
        '--pairs', '2145615:215254',
        '--chains', '2145615:215254:215254:215020',
    ])
    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert CHAINS.match(lines[1])
    assert lines[1].startswith('Hollis St Opp Constitution Hill Library -> ')


def test_bench_bad_way_fails(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A way with an unknown stop or the wrong count is reported."""
    status = load_script().main([
        '--db', str(db), '--repeat', '1', '--pairs', '2145615:215254',
        '--chains', '2145615:215254', 'nowhere:215254:215254:215020',
    ])
    assert status == 1
    assert 'unknown way 2145615:215254' in capsys.readouterr().err


def test_bench_chains_default_is_work_shaped(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`--chains` alone times the WORK-shaped way."""
    load_script().main([
        '--db', str(db), '--repeat', '1', '--pairs', '2145615:215254',
        '--chains',
    ])
    assert '2145615:215254:2145648:214510' in capsys.readouterr().err


@pytest.mark.parametrize(('now', 'expected'), [
    (datetime(2026, 10, 27, 7, 0, tzinfo=SYDNEY), (27, 8)),
    (datetime(2026, 10, 27, 9, 0, tzinfo=SYDNEY), (28, 8)),
    (datetime(2026, 10, 30, 9, 0, tzinfo=SYDNEY), (2, 8)),
])
def test_next_weekday_start(
    now: datetime, expected: tuple[int, int],
) -> None:
    """The default start is the next weekday at 8:00."""
    found = load_script().next_weekday_start(now=now)
    assert (found.day, found.hour) == expected
    assert found.weekday() < 5


def test_bench_times_cached_reads(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`--cached` adds the timing of a read a minute later from the cache."""
    status = load_script().main([
        '--db', str(db),
        '--repeat', '1',
        '--at', '2026-10-16T07:00:00+11:00',
        '--pairs', '2145615:215254',
        '--cached',
    ])
    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert re.match(
        r'^.+ rows, median \d+\.\d ms, max \d+\.\d ms, '
        r'cached median \d+\.\d ms, max \d+\.\d ms$',
        lines[0],
    )
