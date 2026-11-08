"""Record what each timetable source last gave and flush renames."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceState:
    """A source's validators and the SHA-256 of its last zip."""

    etag: str | None
    last_modified: str | None
    sha256: str | None


class SourceError(Exception):
    """A source couldn't be checked or downloaded."""


def load_states(*, path: Path) -> dict[str, SourceState]:
    """
    Read the stored source states.

    Parameters
    ----------
    path : Path
        JSON file of states by source name.

    Returns
    -------
    dict[str, SourceState]
        States by source name; empty when the file is missing.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding='utf-8'))
    return {name: SourceState(**fields) for name, fields in raw.items()}


def save_states(*, path: Path, states: dict[str, SourceState]) -> None:
    """
    Write the source states atomically.

    Parameters
    ----------
    path : Path
        JSON file to replace.
    states : dict[str, SourceState]
        States by source name.
    """
    scratch = path.with_name(f'{path.name}.tmp')
    raw = {name: asdict(state) for name, state in states.items()}
    with scratch.open('w', encoding='utf-8') as file:
        json.dump(raw, file, indent=2, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(scratch, path)


def fsync_dir(*, path: Path) -> None:
    """
    Flush a directory's entries, so renames into it survive a power cut.

    Parameters
    ----------
    path : Path
        Directory.
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
