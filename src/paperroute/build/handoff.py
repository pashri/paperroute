"""Write, check and read the file a build hands over to the extras program."""

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, NoReturn

from paperroute.build.layout import Layout
from paperroute.build.sources import SourceState

HANDOFF_NAME: Final[str] = 'handoff.json'
EXTRAS_MODULE: Final[str] = 'paperroute.build.extras'
STATE_FIELDS: Final[tuple[str, ...]] = ('etag', 'last_modified', 'sha256')


class HandoffError(ValueError):
    """A handoff file is malformed or names a path outside its work dir."""



@dataclass(frozen=True)
class Fetched:
    """The outcome of checking and downloading every source."""

    states: dict[str, SourceState]
    downloaded: tuple[str, ...]
    changed: tuple[str, ...]


@dataclass(frozen=True)
class RefreshTail:
    """
    What a refresh still has to do once its timetable is built.

    Attributes
    ----------
    data_dir : Path
        Holds timetable.db, sources/ and work/.
    fetched : Fetched
        Downloads to store at the swap.
    started : float
        ``time.monotonic()`` when the refresh began.
    error_log : Path | None
        The errors.jsonl file, inside data_dir.
    lock_fd : int
        Descriptor holding the refresh lock.
    """

    data_dir: Path
    fetched: Fetched
    started: float
    error_log: Path | None
    lock_fd: int


@dataclass(frozen=True)
class Handoff:
    """Where a build writes its handoff file, and the refresh tail if any."""

    path: Path
    tail: RefreshTail | None = None


@dataclass(frozen=True)
class HandoffRecord:
    """
    Everything the extras program needs once the build execs into it.

    Attributes
    ----------
    scratch : Path
        Loaded timetable without its run tables.
    out : Path
        Where the finished timetable goes.
    dropped : int
        Rail trips the merge dropped.
    partial : int
        Rail trips the merge dropped on some dates.
    overnight : int
        Feed trips the merge dropped or skipped as overnight twins.
    peak_mb : float
        Peak memory of the load, before the exec.
    tail : RefreshTail | None
        The refresh to finish; None for a plain build.
    """

    scratch: Path
    out: Path
    dropped: int
    partial: int
    overnight: int
    peak_mb: float
    tail: RefreshTail | None


def tail_json(*, tail: RefreshTail) -> dict[str, object]:
    """
    Turn a refresh tail into JSON values.

    Parameters
    ----------
    tail : RefreshTail
        Tail.

    Returns
    -------
    dict[str, object]
        Its fields, paths as strings.
    """
    return {
        'data_dir': str(tail.data_dir),
        'states': {
            name: asdict(state)
            for name, state in tail.fetched.states.items()
        },
        'downloaded': list(tail.fetched.downloaded),
        'changed': list(tail.fetched.changed),
        'started': tail.started,
        'error_log': None if tail.error_log is None else str(tail.error_log),
        'lock_fd': tail.lock_fd,
    }


def field_of[T](*, raw: dict[str, object], key: str, kind: type[T]) -> T:
    """
    Read one field of a handoff file, checking its type.

    Parameters
    ----------
    raw : dict[str, object]
        Parsed JSON object.
    key : str
        Field name.
    kind : type[T]
        Expected type; a bool never passes for an int.

    Returns
    -------
    T
        The value.

    Raises
    ------
    HandoffError
        When the field is missing or of another type.
    """
    value = raw.get(key)
    if not isinstance(value, kind) or (
        isinstance(value, bool) and kind is not bool
    ):
        raise HandoffError(f'handoff field {key} is not a {kind.__name__}')
    return value


def names_of(*, raw: dict[str, object], key: str) -> tuple[str, ...]:
    """
    Read a list of source names from a handoff file.

    Parameters
    ----------
    raw : dict[str, object]
        Parsed JSON object.
    key : str
        Field name.

    Returns
    -------
    tuple[str, ...]
        The names.
    """
    return tuple(
        field_of(raw={'name': name}, key='name', kind=str)
        for name in field_of(raw=raw, key=key, kind=list)
    )


def state_of(*, name: str, raw: object) -> SourceState:
    """
    Read one source state from a handoff file.

    Parameters
    ----------
    name : str
        Source name, for the error message.
    raw : object
        Parsed state.

    Returns
    -------
    SourceState
        The state.

    Raises
    ------
    HandoffError
        When it lacks a field, has another, or a value is not a str or None.
    """
    if not isinstance(raw, dict) or set(raw) != set(STATE_FIELDS):
        raise HandoffError(f'bad source state for {name}')
    values = [raw[key] for key in STATE_FIELDS]
    if not all(value is None or isinstance(value, str) for value in values):
        raise HandoffError(f'bad source state values for {name}')
    etag, last_modified, sha256 = values
    return SourceState(etag=etag, last_modified=last_modified, sha256=sha256)


def states_of(*, raw: dict[str, object]) -> dict[str, SourceState]:
    """
    Read the source states from a handoff file.

    Parameters
    ----------
    raw : dict[str, object]
        Parsed tail object.

    Returns
    -------
    dict[str, SourceState]
        States by source name.
    """
    return {
        str(name): state_of(name=str(name), raw=fields)
        for name, fields in field_of(
            raw=raw, key='states', kind=dict,
        ).items()
    }


def tail_of(*, raw: dict[str, object]) -> RefreshTail:
    """
    Read a refresh tail back from JSON values.

    Parameters
    ----------
    raw : dict[str, object]
        Values from tail_json.

    Returns
    -------
    RefreshTail
        The tail.
    """
    error_log = raw.get('error_log')
    return RefreshTail(
        data_dir=Path(field_of(raw=raw, key='data_dir', kind=str)),
        fetched=Fetched(
            states=states_of(raw=raw),
            downloaded=names_of(raw=raw, key='downloaded'),
            changed=names_of(raw=raw, key='changed'),
        ),
        started=field_of(raw=raw, key='started', kind=float),
        error_log=(
            None
            if error_log is None
            else Path(field_of(raw=raw, key='error_log', kind=str))
        ),
        lock_fd=field_of(raw=raw, key='lock_fd', kind=int),
    )


def record_json(*, record: HandoffRecord) -> dict[str, object]:
    """
    Turn a handoff record into JSON values.

    Parameters
    ----------
    record : HandoffRecord
        Record.

    Returns
    -------
    dict[str, object]
        Its fields, paths as strings.
    """
    return {
        'scratch': str(record.scratch),
        'out': str(record.out),
        'dropped': record.dropped,
        'partial': record.partial,
        'overnight': record.overnight,
        'peak_mb': record.peak_mb,
        'tail': None if record.tail is None else tail_json(tail=record.tail),
    }


def record_of(*, raw: dict[str, object]) -> HandoffRecord:
    """
    Read a handoff record back from JSON values.

    Parameters
    ----------
    raw : dict[str, object]
        Values from record_json.

    Returns
    -------
    HandoffRecord
        The record.
    """
    tail = raw.get('tail')
    return HandoffRecord(
        scratch=Path(field_of(raw=raw, key='scratch', kind=str)),
        out=Path(field_of(raw=raw, key='out', kind=str)),
        dropped=field_of(raw=raw, key='dropped', kind=int),
        partial=field_of(raw=raw, key='partial', kind=int),
        overnight=field_of(raw=raw, key='overnight', kind=int),
        peak_mb=field_of(raw=raw, key='peak_mb', kind=float),
        tail=(
            None
            if tail is None
            else tail_of(raw=field_of(raw=raw, key='tail', kind=dict))
        ),
    )


def inside(*, path: Path, folder: Path) -> bool:
    """
    Tell whether a path resolves to somewhere inside a folder.

    Parameters
    ----------
    path : Path
        Path to test.
    folder : Path
        Folder, already resolved.

    Returns
    -------
    bool
        True when path is within folder.
    """
    return path.resolve().is_relative_to(folder)


def check_record(*, record: HandoffRecord, path: Path) -> None:
    """
    Check every path in a record lies inside the handoff file's work dir.

    For a refresh, that work dir must be the data dir's work/ and the
    error log must be inside the data dir.

    Parameters
    ----------
    record : HandoffRecord
        Record to check.
    path : Path
        Handoff file; its folder is the work dir.

    Raises
    ------
    HandoffError
        When a path lies elsewhere.
    """
    work = path.parent.resolve()
    if not all(
        inside(path=place, folder=work)
        for place in (record.scratch, record.out)
    ):
        raise HandoffError(f'handoff paths must be inside {work}')
    tail = record.tail
    if tail is None:
        return
    if Layout(data_dir=tail.data_dir).work.resolve() != work:
        raise HandoffError(f'handoff data dir does not own {work}')
    if tail.error_log is not None and not inside(
        path=tail.error_log, folder=tail.data_dir.resolve(),
    ):
        raise HandoffError('handoff error log must be inside the data dir')


def write_handoff(*, path: Path, record: HandoffRecord) -> None:
    """
    Check a record and write it as the handoff file.

    Parameters
    ----------
    path : Path
        Handoff file to write.
    record : HandoffRecord
        What the extras program needs.
    """
    check_record(record=record, path=path)
    path.write_text(
        json.dumps(record_json(record=record), indent=2),
        encoding='utf-8',
    )


def read_handoff(*, path: Path) -> HandoffRecord:
    """
    Read and check a handoff file.

    Parameters
    ----------
    path : Path
        Handoff file.

    Returns
    -------
    HandoffRecord
        The record, every path inside the file's work dir.

    Raises
    ------
    HandoffError
        When the file is not a JSON object or fails check_record.
    """
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise HandoffError('handoff file is not a JSON object')
    record = record_of(raw=raw)
    check_record(record=record, path=path)
    return record


def exec_python(*, args: Sequence[str], keep: Sequence[int]) -> NoReturn:
    """
    Replace this process with a new Python, keeping some descriptors open.

    The pid, stdout and stderr stay the same. Python opens files
    close-on-exec, so any descriptor to survive must be listed in keep.

    Parameters
    ----------
    args : Sequence[str]
        Arguments after the interpreter, such as ``['-m', module]``.
    keep : Sequence[int]
        Descriptors to keep open across the exec.
    """
    for descriptor in keep:
        os.set_inheritable(descriptor, True)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *args])


def hand_over(*, handoff: Handoff, record: HandoffRecord) -> NoReturn:
    """
    Write the handoff file and exec into the extras program.

    Parameters
    ----------
    handoff : Handoff
        Where to write the file.
    record : HandoffRecord
        What the extras program needs.
    """
    write_handoff(path=handoff.path, record=record)
    exec_python(
        args=['-m', EXTRAS_MODULE, str(record.scratch), str(handoff.path)],
        keep=() if record.tail is None else (record.tail.lock_fd,),
    )
