"""Replace a file's contents in one step, so a power cut leaves old or new."""

import contextlib
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

FILE_MODE: Final[int] = 0o644


def write_atomic(
    *,
    path: Path,
    text: str,
    prefix: str,
    sync: Callable[[Path], None] | None = None,
    mode: int = FILE_MODE,
) -> None:
    """
    Replace a file so a reader sees the old text or the new, never a mix.

    Parameters
    ----------
    path : Path
        The file.
    text : str
        The new contents.
    prefix : str
        Name prefix of the temp file written beside it.
    sync : Callable[[Path], None] | None
        Flushes the directory after the rename; `sync_directory` when None.
        A failure here is logged, not raised, since the new file is
        already in place.
    mode : int
        Permission bits of the new file; readable by everyone by default.

    Raises
    ------
    OSError
        When writing or renaming fails; the temp file is removed.
    """
    replace_through_temp(path=path, text=text, mode=mode, prefix=prefix)
    try:
        (sync or sync_directory)(path.parent)
    except OSError:
        logger.warning('%s directory not synced', path.name, exc_info=True)


def replace_through_temp(
    *, path: Path, text: str, mode: int, prefix: str,
) -> None:
    """
    Write a temp file beside a file, then rename it over the file.

    Parameters
    ----------
    path : Path
        The file.
    text : str
        The new contents.
    mode : int
        Permission bits of the new file.
    prefix : str
        Name prefix of the temp file.

    Raises
    ------
    OSError
        When writing or renaming fails; the temp file is removed.
    """
    descriptor, temp_name = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    try:
        write_synced(descriptor=descriptor, text=text, mode=mode)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def write_synced(*, descriptor: int, text: str, mode: int) -> None:
    """
    Write text to an open file, set its mode and flush it to disk.

    Parameters
    ----------
    descriptor : int
        The open file; closed after.
    text : str
        The contents.
    mode : int
        Permission bits.
    """
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def sync_directory(directory: Path) -> None:
    """
    Flush a directory's entries to disk.

    Parameters
    ----------
    directory : Path
        The directory.
    """
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
