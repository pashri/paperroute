"""Tests for Sydney's build entry."""

import json
import logging
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_sydney.build.__main__ import main
from tests.build.network_feed import network_feed


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
    """A missing key file reaches errors.jsonl, exits 1 and builds nothing."""
    key_file = tmp_path / 'no-such-key'
    error_log = tmp_path / 'log' / 'errors.jsonl'
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_TFNSW', str(key_file))
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'paperroute-build', 'refresh', '--data-dir', str(tmp_path),
            '--error-log', str(error_log),
        ],
    )
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 1
    lines = error_log.read_text(encoding='utf-8').splitlines()
    records = [json.loads(line) for line in lines]
    # one line per source's HEAD, then the refresh with nothing to fall back on
    assert [record['msg'] for record in records] == [
        'sydneytrains: HEAD failed, using the stored zip',
        'metro: HEAD failed, using the stored zip',
        'bundle: HEAD failed, using the stored zip',
        'refresh failed',
    ]
    assert all(record['level'] == 'ERROR' for record in records)
    assert f'No TfNSW API key in {key_file}' in records[0]['exc_info']
    assert 'no stored zip' in records[-1]['exc_info']
    assert not (tmp_path / 'timetable.db').exists()
    assert not any((tmp_path / 'sources').glob('*'))


def test_gtfs_execs_into_the_extras(tmp_path: Path) -> None:
    """The gtfs build hands over to extras, which finishes the file."""
    out = tmp_path / 'timetable.db'
    result = subprocess.run(
        [
            sys.executable, '-m', 'paperroute_sydney.build', 'gtfs',
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
