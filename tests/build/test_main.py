"""Tests for the build command line."""

import json
import logging
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from paperroute.build.__main__ import main, parse_args
from paperroute.core.model import SYDNEY
from tests.build.network_feed import network_feed
from tests.build.osm_factory import TINY_BBOX, write_tiny_osm
from tests.build.tile_inputs import write_timetable


def test_start_defaults_to_yesterday(monkeypatch: pytest.MonkeyPatch) -> None:
    """The window starts a day early to keep NightRide trips."""
    monkeypatch.setattr(
        sys,
        'argv',
        ['build', 'gtfs', '--bundle', 'b.zip', '--out', 'o.db'],
    )
    today = datetime.now(SYDNEY).date()
    start = parse_args().start
    assert start in (
        today - timedelta(days=1),
        today - timedelta(days=2),
    )


def test_tiles_cli_parses_bbox_and_zooms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tiles subcommand reads its box, zooms and defaults."""
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'build', 'tiles', '--osm', 'nsw.osm.pbf',
            '--timetable', 'timetable.db', '--out', 'data/tiles.db',
            '--bbox', '150.95,-33.84,151.03,-33.76', '--zooms', '8,45',
        ],
    )
    args = parse_args()
    assert args.bbox == (150.95, -33.84, 151.03, -33.76)
    assert args.zooms == (8, 45)
    assert args.work is None
    assert args.jobs == (os.cpu_count() or 1)
    assert args.index == 'flex_mem'


def test_tiles_cli_refuses_a_short_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A box needs four numbers."""
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'build', 'tiles', '--osm', 'a', '--timetable', 'b',
            '--out', 'c', '--bbox', '150.95,-33.84,151.03',
        ],
    )
    with pytest.raises(SystemExit):
        parse_args()


def test_refresh_takes_data_dir_and_error_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh subcommand reads its paths, days and force flag."""
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'build',
            'refresh',
            '--data-dir',
            '/var/lib/paperroute',
            '--error-log',
            '/var/lib/paperroute/log/errors.jsonl',
        ],
    )
    args = parse_args()
    assert args.data_dir == Path('/var/lib/paperroute')
    assert args.error_log == Path('/var/lib/paperroute/log/errors.jsonl')
    assert args.days == 14
    assert not args.force


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """Put the root logger back after a test that configures it."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.usefixtures('restored_logging')
def test_refresh_logs_a_missing_key_file_to_the_error_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing key file reaches errors.jsonl and exits 1."""
    key_file = tmp_path / 'no-such-key'
    error_log = tmp_path / 'log' / 'errors.jsonl'
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(key_file))
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'build', 'refresh', '--data-dir', str(tmp_path),
            '--error-log', str(error_log),
        ],
    )
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 1
    lines = error_log.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['level'] == 'ERROR'
    assert f'No TfNSW API key in {key_file}' in record['exc_info']


def tiles_argv(*, tmp_path: Path, osm: Path) -> list[str]:
    """
    Build a tiles command line over the tiny inputs.

    Parameters
    ----------
    tmp_path : Path
        Directory holding the timetable and the output.
    osm : Path
        The OSM file to read.

    Returns
    -------
    list[str]
        The arguments, with one job and the default work directory.
    """
    write_timetable(path=tmp_path / 'timetable.db')
    return [
        'build', 'tiles', '--osm', str(osm),
        '--timetable', str(tmp_path / 'timetable.db'),
        '--out', str(tmp_path / 'tiles.db'),
        '--bbox', ','.join(str(value) for value in TINY_BBOX),
        '--zooms', '8', '--jobs', '1',
    ]


@pytest.mark.usefixtures('restored_logging')
def test_tiles_prints_a_json_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A finished build prints its report as the last line and exits 0."""
    write_tiny_osm(path=tmp_path / 'tiny.osm.pbf')
    monkeypatch.setattr(
        sys,
        'argv',
        tiles_argv(tmp_path=tmp_path, osm=tmp_path / 'tiny.osm.pbf'),
    )
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report['tiles'] > 0
    assert (tmp_path / 'tiles-work').is_dir()


@pytest.mark.usefixtures('restored_logging')
def test_tiles_logs_a_failed_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing OSM file is logged and exits 1."""
    monkeypatch.setattr(
        sys,
        'argv',
        tiles_argv(tmp_path=tmp_path, osm=tmp_path / 'missing.osm.pbf'),
    )
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 1
    lines = capsys.readouterr().err.splitlines()
    assert json.loads(lines[-1])['msg'] == 'tile build failed'
    assert not (tmp_path / 'tiles.db').exists()


def test_gtfs_execs_into_the_extras(tmp_path: Path) -> None:
    """The gtfs build hands over to extras, which finishes the file."""
    out = tmp_path / 'timetable.db'
    result = subprocess.run(
        [
            sys.executable, '-m', 'paperroute.build', 'gtfs',
            '--bundle', str(network_feed(path=tmp_path / 'network.zip')),
            '--out', str(out), '--start', '2026-10-05', '--days', '3',
        ],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-1] == 'progress done dropped=0 partial=0 overnight=0'
    assert lines[-2].startswith('progress extras peak ')
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        'network.zip', 'timetable.db',
    ]
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] > 0
