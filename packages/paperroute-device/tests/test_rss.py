"""Tests for peak memory measurement."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.rss import current_rss_mb, max_rss_mb, peak_rss_mb

CHILD_PEAK: Final[str] = (
    'import subprocess, sys\n'
    'from paperroute_device.rss import max_rss_mb\n'
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


def test_current_rss_mb_asks_ps_without_statm(tmp_path: Path) -> None:
    """Without a statm file the live size comes from ps, below the peak."""
    live = current_rss_mb(statm=tmp_path / 'missing')
    assert 1.0 < live <= max_rss_mb() + 1.0


def test_current_rss_mb_reads_statm(tmp_path: Path) -> None:
    """The resident page count is the second field of statm."""
    statm = tmp_path / 'statm'
    pages = (64 << 20) // os.sysconf('SC_PAGE_SIZE')
    statm.write_text(f'100000 {pages} 300 1 0 500 0\n')
    assert current_rss_mb(statm=statm) == 64.0


def test_peak_rss_mb_reads_vmhwm(tmp_path: Path) -> None:
    """On Linux the peak is VmHWM, which an exec resets."""
    status = tmp_path / 'status'
    status.write_text('Name:\tpython\nVmHWM:\t   73728 kB\n', encoding='ascii')
    assert peak_rss_mb(status=status) == 72.0
    status.write_text('Name:\tpython\n', encoding='ascii')
    with pytest.raises(ValueError, match='VmHWM'):
        peak_rss_mb(status=status)


def test_peak_rss_mb_falls_back_to_rusage(tmp_path: Path) -> None:
    """Without a status file the peak is this process's ru_maxrss."""
    assert 1.0 < peak_rss_mb(status=tmp_path / 'missing') <= max_rss_mb()
