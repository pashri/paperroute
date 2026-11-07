"""Tests for replacing a file in one step."""

import logging
from pathlib import Path

import pytest

from paperroute_device.adapters.atomic_file import write_atomic


def failing_sync(directory: Path) -> None:
    """
    Fail the way a directory fsync can.

    Parameters
    ----------
    directory : Path
        The directory.

    Raises
    ------
    OSError
        Always.
    """
    raise OSError(f'cannot sync {directory}')


def test_directory_sync_failure_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The rename has landed, so a sync failure only warns."""
    path = tmp_path / 'file.json'
    write_atomic(path=path, text='{}', prefix='.file.', sync=failing_sync)
    assert path.read_text() == '{}'
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
