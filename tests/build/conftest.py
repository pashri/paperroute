"""Fixtures shared by the build tests."""

import fcntl
import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(name='data_dir')
def fixture_data_dir(tmp_path: Path) -> Path:
    """
    A data dir holding an old timetable and an empty work dir.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Returns
    -------
    Path
        The data dir.
    """
    data_dir = tmp_path / 'data'
    (data_dir / 'work').mkdir(parents=True)
    (data_dir / 'sources').mkdir()
    (data_dir / 'timetable.db').write_bytes(b'old timetable')
    return data_dir


@pytest.fixture(name='lock_fd')
def fixture_lock_fd(data_dir: Path) -> Iterator[int]:
    """
    Hold the data dir's refresh lock on a raw descriptor.

    Parameters
    ----------
    data_dir : Path
        Data dir.

    Yields
    ------
    int
        The descriptor; the tail closes it.
    """
    fd = os.open(data_dir / 'refresh.lock', os.O_WRONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield fd
    try:
        os.close(fd)
    except OSError:
        pass


@pytest.fixture(name='synced')
def fixture_synced(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """
    Record the inode of every file descriptor passed to fsync.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Patches fsync for the test.

    Returns
    -------
    list[int]
        The inodes, filled as the test runs.
    """
    inodes: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        inodes.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', fsync)
    return inodes
