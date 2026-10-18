"""Tests for peak memory measurement."""

import subprocess
import sys
from typing import Final

from paperroute.rss import max_rss_mb

CHILD_PEAK: Final[str] = (
    'import subprocess, sys\n'
    'from paperroute.rss import max_rss_mb\n'
    'assert max_rss_mb() < 100, max_rss_mb()\n'
    "subprocess.run([sys.executable, '-c', \"b'x' * (100 << 20)\"],"
    ' check=True)\n'
    'assert max_rss_mb() >= 100, max_rss_mb()\n'
)


def test_max_rss_mb_is_positive() -> None:
    """Peak memory is a positive number of megabytes."""
    assert 1.0 < max_rss_mb() < 100_000.0


def test_max_rss_covers_children() -> None:
    """A finished child that touched 100 MB lifts the parent's peak."""
    result = subprocess.run(
        [sys.executable, '-c', CHILD_PEAK],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
