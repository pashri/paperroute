"""Check that saved state files load, and print a digest of each."""

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
from datetime import date, time
from enum import StrEnum
from pathlib import Path
from typing import Final

from paperroute.adapters.state_file import BAD_PREFIX, JsonStateRepository
from paperroute.core.saved import Saved
from paperroute_transit.core.auto_model import AutoState
from paperroute_transit.core.model import Pin, pin_ref

BACKUPS: Final[Path] = Path('~/Code/pashri/paperroute-backups').expanduser()
STATE_NAME: Final[str] = 'state.json'


def repository(*, path: Path) -> JsonStateRepository:
    """
    Build the repository that reads and writes one state file.

    Parameters
    ----------
    path : Path
        The state file.

    Returns
    -------
    JsonStateRepository
        Its repository.
    """
    return JsonStateRepository(path=path)


def plain(value: object) -> object:
    """
    Turn a value into something JSON can hold.

    Parameters
    ----------
    value : object
        A dataclass, enum, time, date, sequence, mapping or scalar.

    Returns
    -------
    object
        Dicts, lists, strings, numbers, booleans and None.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [plain(item) for item in value]
        return items if isinstance(value, (tuple, list)) else sorted(
            items, key=json.dumps,
        )
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


def pin_digest(*, pin: Pin) -> dict[str, object]:
    """
    Sum up one pin.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    dict[str, object]
        Its ref, nickname, kind, end stop IDs, route lists and how many
        ways and windows it has.
    """
    destination = pin.destination
    return {
        'ref': pin_ref(pin=pin),
        'nickname': pin.nickname,
        'kind': None if pin.kind is None else str(pin.kind),
        'ends': [
            pin.origin.stop_id,
            None if destination is None else destination.stop_id,
        ],
        'routes': list(pin.routes),
        'only': list(pin.only),
        'never': list(pin.never),
        'ways': len(pin.ways),
        'windows': len(pin.windows),
    }


def auto_digest(*, auto: AutoState) -> dict[str, object]:
    """
    Sum up the automatic pin's state by refs.

    Parameters
    ----------
    auto : AutoState
        Auto state.

    Returns
    -------
    dict[str, object]
        The running occurrence's ref, and the ref and pinned flag of the
        place shown before it.
    """
    occurrence, before = auto.occurrence, auto.before
    return {
        'occurrence': None if occurrence is None else occurrence.ref,
        'before': None if before is None else pin_ref(pin=before.place),
        'before_pinned': None if before is None else before.pinned,
    }


def digest(*, saved: Saved) -> dict[str, object]:
    """
    Sum up a saved state.

    Parameters
    ----------
    saved : Saved
        The saved state.

    Returns
    -------
    dict[str, object]
        Pins, current index, unpinned ref, view, settings, quota and auto
        refs, ready for JSON. No schema version.
    """
    unpinned = saved.unpinned
    return {
        'pins': [pin_digest(pin=pin) for pin in saved.pins],
        'current': saved.current,
        'unpinned': None if unpinned is None else pin_ref(pin=unpinned),
        'view': str(saved.view),
        'settings': plain(saved.settings),
        'quota': plain(saved.quota),
        'auto': auto_digest(auto=saved.auto),
    }


def check_file(
    *, path: Path, work: Path,
) -> tuple[dict[str, object] | None, list[str]]:
    """
    Load a copy of a state file and check it saves stably.

    Parameters
    ----------
    path : Path
        The state file; it is only read.
    work : Path
        An empty directory to copy it into.

    Returns
    -------
    tuple[dict[str, object] | None, list[str]]
        The digest, None when the file did not load as stored, and the
        problems found.
    """
    copy = work / STATE_NAME
    shutil.copyfile(path, copy)
    files = repository(path=copy)
    loaded = files.load()
    problems = [] if loaded.stored else ['not stored']
    if any(work.glob(f'{BAD_PREFIX}*')):
        problems.append('moved aside')
    if problems:
        return None, problems
    files.save(saved=loaded.saved)
    first = copy.read_bytes()
    files.save(saved=files.load().saved)
    if copy.read_bytes() != first:
        problems.append('unstable save')
    return digest(saved=loaded.saved), problems


def main(argv: list[str] | None = None) -> int:
    """
    Check every ``state-*.json`` in a folder and print their digests.

    Parameters
    ----------
    argv : list[str] | None
        Arguments; `sys.argv` when None.

    Returns
    -------
    int
        0 when every file is clean, else 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', type=Path, default=BACKUPS)
    args = parser.parse_args(argv)
    failed = False
    for path in sorted(args.dir.glob('state-*.json')):
        with tempfile.TemporaryDirectory() as work:
            found, problems = check_file(path=path, work=Path(work))
        for problem in problems:
            print(f'{path.name}: {problem}', file=sys.stderr)
        failed = failed or bool(problems)
        if found is not None:
            print(json.dumps({'file': path.name, **found}, sort_keys=True))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
