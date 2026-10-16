"""Tests for the build command line."""

import sys
from datetime import datetime, timedelta

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
