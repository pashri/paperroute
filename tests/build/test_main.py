"""Tests for the build command line."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from paperroute.build.__main__ import parse_args
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
