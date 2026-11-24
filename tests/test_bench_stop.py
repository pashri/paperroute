"""Tests for the Stop view tick benchmark script."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.helpers import slice_db

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'bench_stop.py'
STOP = '2145615'
LINE = r'^{name}: median [\d.]+ ms, p90 [\d.]+ ms$'


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
        The timetable.
    """
    return slice_db(tmp_path=tmp_path_factory.mktemp('bench_stop'))


def small_args(*, db: Path) -> list[str]:
    """
    Arguments for two repeats over the small timetable.

    Parameters
    ----------
    db : Path
        The timetable.

    Returns
    -------
    list[str]
        The command-line arguments.
    """
    return [
        '--city', 'sydney',
        '--db', str(db),
        '--stop', STOP,
        '--at', '2026-10-16T07:00:00+11:00',
        '--repeat', '2',
    ]


def test_bench_stop_prints_median_and_p90(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Both modes print a median and a p90, and the overhead."""
    assert load_script().main(argv=small_args(db=db)) == 0
    out = capsys.readouterr().out
    for name in ('with gauge', 'today'):
        assert re.search(LINE.format(name=name), out, re.M)
    assert re.search(r'^overhead: [+-][\d.]+%$', out, re.M)


def test_bench_stop_one_mode_prints_only_that_mode(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`--mode today` times the Stop view without the gauge buses only."""
    argv = [*small_args(db=db), '--mode', 'today']
    assert load_script().main(argv=argv) == 0
    out = capsys.readouterr().out
    assert re.search(LINE.format(name='today'), out, re.M)
    assert 'with gauge' not in out
    assert 'overhead' not in out


def test_bench_stop_exits_2_when_over_the_limit(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """An impossible limit puts the gauge over, and the exit is 2."""
    argv = [*small_args(db=db), '--limit', '-100']
    assert load_script().main(argv=argv) == 2
    assert 'over the limit' in capsys.readouterr().out


def test_bench_stop_unknown_stop_fails(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown stop exits 1."""
    status = load_script().main(argv=[*small_args(db=db), '--stop', 'x'])
    assert status == 1
    assert 'unknown stop x' in capsys.readouterr().err


def test_bench_stop_no_departures_fails(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A stop with no departures at the time exits 1."""
    argv = [*small_args(db=db), '--at', '2020-01-01T07:00:00+11:00']
    assert load_script().main(argv=argv) == 1
    assert 'no departures from' in capsys.readouterr().err


def test_bench_stop_missing_timetable_fails(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing timetable exits 1."""
    argv = [*small_args(db=db), '--db', str(tmp_path / 'a.db')]
    assert load_script().main(argv=argv) == 1
    assert 'no timetable at' in capsys.readouterr().err


def test_bench_stop_decodes_a_saved_positions_feed(
    db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`--positions` prints the decode time and still times both modes."""
    feed = Path(__file__).parent / 'fixtures' / 'real'
    argv = [
        *small_args(db=db),
        '--positions', str(feed / 'v1_gtfs_vehiclepos_buses.pb'),
    ]
    assert load_script().main(argv=argv) == 0
    out = capsys.readouterr().out
    assert re.search(r'^feed decode: median [\d.]+ ms$', out, re.M)
    assert re.search(LINE.format(name='with gauge'), out, re.M)


def test_bench_stop_missing_positions_fails(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing positions file exits 1."""
    argv = [*small_args(db=db), '--positions', str(tmp_path / 'x.pb')]
    assert load_script().main(argv=argv) == 1
    assert 'no positions at' in capsys.readouterr().err
