"""Tests for the build command line."""

import json
import logging
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from paperroute.build.__main__ import main, parse_args
from paperroute.core.model import SYDNEY


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
    assert json.loads(lines[0])['level'] == 'ERROR'
