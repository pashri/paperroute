"""Tests for the map timing benchmark script."""

import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

from paperroute_transit.build.tile_store import TileWriter
from tests.helpers import slice_db

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'bench_map.py'
STAGES = (
    'day lines',
    'shapes and patterns',
    'tick',
    'framing',
    'view cold',
    'view',
    'base',
    'frame',
)
STOP = '2145615'


def load_script() -> ModuleType:
    """
    Import the benchmark script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location('bench_map', SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules['bench_map'] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name='files', scope='module')
def _files(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """
    The real GTFS slice as a database, and an empty tiles file.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the working directory.

    Returns
    -------
    tuple[Path, Path]
        The timetable and the tiles file.
    """
    folder = tmp_path_factory.mktemp('bench')
    tiles = folder / 'tiles.db'
    with TileWriter(out=tiles) as writer:
        writer.finish(meta={})
    return slice_db(tmp_path=folder), tiles


def small_args(*, files: tuple[Path, Path]) -> list[str]:
    """
    Arguments for one repeat over the small timetable and tiles.

    Parameters
    ----------
    files : tuple[Path, Path]
        The timetable and the tiles file.

    Returns
    -------
    list[str]
        The command-line arguments.
    """
    return [
        '--city', 'sydney',
        '--db', str(files[0]),
        '--tiles', str(files[1]),
        '--stop', STOP,
        '--at', '2026-10-16T07:00:00+11:00',
        '--repeat', '1',
    ]


def test_bench_prints_each_stage(
    files: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    """The bench prints one timing line per stage and the counts."""
    assert load_script().main(argv=small_args(files=files)) == 0
    out = capsys.readouterr().out
    for name in STAGES:
        assert re.search(
            rf'^{name}: [\d.]+ ms, [\d.]+ ms \(budget \d+ ms\)$',
            out,
            re.M,
        )
    assert re.search(r'^lines \d+, rings \d+$', out, re.M)


def test_bench_unknown_stop_fails(
    files: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown stop exits 1."""
    status = load_script().main(
        argv=[*small_args(files=files), '--stop', 'x'],
    )
    assert status == 1
    assert 'unknown stop x' in capsys.readouterr().err


def test_bench_no_departures_fails(
    files: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    """A stop with no departures at the time exits 1."""
    status = load_script().main(
        argv=[*small_args(files=files), '--at', '2020-01-01T07:00:00+11:00'],
    )
    assert status == 1
    assert 'no departures from' in capsys.readouterr().err


def test_bench_missing_files_fail(
    files: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing timetable or tiles file exits 1."""
    module = load_script()
    args = small_args(files=files)
    assert module.main(argv=[*args, '--db', str(tmp_path / 'a.db')]) == 1
    assert module.main(argv=[*args, '--tiles', str(tmp_path / 'b.db')]) == 1
    err = capsys.readouterr().err
    assert 'no timetable at' in err
    assert 'no tiles at' in err


def test_next_weekday_evening_skips_the_weekend() -> None:
    """The default start is 17:30 on the next weekday."""
    zone = ZoneInfo('Australia/Sydney')
    found = load_script().next_weekday_start(
        now=datetime(2026, 10, 30, 18, 0, tzinfo=zone),
    )
    assert (found.day, found.hour, found.minute) == (2, 17, 30)


def test_bench_exits_2_when_a_median_is_over_its_budget(
    files: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    """A tiny budget scale puts every stage over, and the exit is 2."""
    status = load_script().main(
        argv=[*small_args(files=files), '--budget-scale', '0.000001'],
    )
    assert status == 2
    assert re.search(r'^lines \d+, rings \d+$', capsys.readouterr().out, re.M)


def test_bench_budgets_are_named_per_stage() -> None:
    """Each stage has a budget; a busy stop's day lines get more."""
    module = load_script()
    assert set(module.BUDGETS) == set(STAGES)
    assert module.BUDGETS['view cold'] == 40
    assert module.day_budget(lines=31) == 300
    assert module.day_budget(lines=30) == 150


def test_bench_no_departures_without_at_names_the_default(
    files: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    """With no `--at`, the message gives the default start and the fix."""
    args = small_args(files=files)
    start = args.index('--at')
    status = load_script().main(argv=args[:start] + args[start + 2:])
    assert status == 1
    err = capsys.readouterr().err
    assert 'next weekday at 17:30' in err
    assert '--at' in err
