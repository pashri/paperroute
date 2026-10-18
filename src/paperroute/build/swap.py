"""Check a built timetable, swap it in and finish a refresh."""

import fcntl
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
import zipfile
from collections.abc import Callable, Sequence
from contextlib import closing, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, NoReturn

from paperroute.build.sources import (
    SourceError,
    SourceState,
    fsync_dir,
    save_states,
)
from paperroute.logs import configure_logging
from paperroute.rss import max_rss_mb

logger = logging.getLogger(__name__)

MIN_TRIPS: Final[int] = 1000
HANDOFF_NAME: Final[str] = 'handoff.json'
EXTRAS_MODULE: Final[str] = 'paperroute.build.extras'


class TimetableCheckError(Exception):
    """A newly built timetable failed its integrity check."""


class HandoffError(ValueError):
    """A handoff file is malformed or names a path outside its work dir."""


class LockLostError(OSError):
    """The refresh lock was not held after the handoff."""


REFRESH_ERRORS: Final[tuple[type[Exception], ...]] = (
    SourceError,
    TimetableCheckError,
    sqlite3.Error,
    zipfile.BadZipFile,
    OSError,
    KeyError,
    ValueError,
)


@dataclass(frozen=True)
class RefreshReport:
    """What a refresh did."""

    downloaded: tuple[str, ...]
    changed: tuple[str, ...]
    rebuilt: bool
    seconds: float
    max_rss_mb: float


@dataclass(frozen=True)
class Fetched:
    """The outcome of checking and downloading every source."""

    states: dict[str, SourceState]
    downloaded: tuple[str, ...]
    changed: tuple[str, ...]


@dataclass(frozen=True)
class Layout:
    """Paths under the data directory."""

    data_dir: Path

    @property
    def work(self) -> Path:
        """
        Build scratch directory.

        Returns
        -------
        Path
            The work directory under data_dir.
        """
        return self.data_dir / 'work'

    @property
    def sources(self) -> Path:
        """
        Directory of the last good zips and their states.

        Returns
        -------
        Path
            The sources directory under data_dir.
        """
        return self.data_dir / 'sources'

    @property
    def states(self) -> Path:
        """
        Stored source states.

        Returns
        -------
        Path
            The sources.json file in the sources directory.
        """
        return self.sources / 'sources.json'

    @property
    def timetable(self) -> Path:
        """
        Live timetable.

        Returns
        -------
        Path
            The timetable.db file under data_dir.
        """
        return self.data_dir / 'timetable.db'

    @property
    def lock(self) -> Path:
        """
        Lock file held for the whole refresh.

        Returns
        -------
        Path
            The refresh.lock file under data_dir.
        """
        return self.data_dir / 'refresh.lock'


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
    peak_mb : float
        Peak memory of the load, before the exec.
    tail : RefreshTail | None
        The refresh to finish; None for a plain build.
    """

    scratch: Path
    out: Path
    dropped: int
    partial: int
    peak_mb: float
    tail: RefreshTail | None


def progress(*, phase: str) -> None:
    """
    Print a progress line.

    Parameters
    ----------
    phase : str
        What the refresh is doing.
    """
    print(f'progress {phase}', flush=True)


def clear_work(*, work: Path) -> None:
    """
    Empty the scratch directory, keeping the directory itself.

    Parameters
    ----------
    work : Path
        Scratch directory, created if missing.
    """
    work.mkdir(parents=True, exist_ok=True)
    for entry in work.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def check_timetable(*, path: Path) -> None:
    """
    Check a built timetable before it goes live.

    Parameters
    ----------
    path : Path
        Built timetable.

    Raises
    ------
    TimetableCheckError
        If SQLite's integrity check fails or it has under MIN_TRIPS trips.
    """
    progress(phase='check')
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        (verdict,) = conn.execute('PRAGMA integrity_check').fetchone()
        (trips,) = conn.execute('SELECT count(*) FROM trips').fetchone()
    if verdict != 'ok':
        raise TimetableCheckError(f'integrity_check: {verdict}')
    if trips < MIN_TRIPS:
        raise TimetableCheckError(f'{trips} trips, want {MIN_TRIPS}')


def swap_in(*, layout: Layout, built: Path, fetched: Fetched) -> None:
    """
    Make a checked timetable live, then store the zips it came from.

    The timetable is replaced before the states are saved, so a crash in
    between leaves the old validators and the next run rebuilds.

    Parameters
    ----------
    layout : Layout
        Data paths.
    built : Path
        Checked timetable in the scratch directory.
    fetched : Fetched
        Downloads to store.
    """
    progress(phase='swap')
    os.replace(built, layout.timetable)
    fsync_dir(path=layout.data_dir)
    for name in fetched.changed:
        os.replace(layout.work / f'{name}.zip', layout.sources / f'{name}.zip')
    fsync_dir(path=layout.sources)
    save_states(path=layout.states, states=fetched.states)


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
        Expected type.

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
    if not isinstance(value, kind):
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

    Raises
    ------
    HandoffError
        When a state has the wrong fields.
    """
    states = field_of(raw=raw, key='states', kind=dict)
    try:
        return {
            str(name): SourceState(**fields)
            for name, fields in states.items()
        }
    except TypeError as error:
        raise HandoffError(f'bad source state: {error}') from error


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


def holds_lock(*, fd: int, path: Path) -> bool:
    """
    Tell whether a descriptor is open on a lock file that is locked.

    Parameters
    ----------
    fd : int
        Descriptor that should hold the lock.
    path : Path
        Lock file.

    Returns
    -------
    bool
        True when fd is open on path and a second lock attempt is refused.
    """
    try:
        same = os.path.samestat(os.fstat(fd), path.stat())
    except OSError:
        return False
    with path.open('a') as other:
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return same
    return False


def guarded[T](
    *, step: Callable[[], T],
    passthrough: tuple[type[Exception], ...] = (),
) -> T | None:
    """
    Run a refresh step, logging any failure to the error log.

    Parameters
    ----------
    step : Callable[[], T]
        The step.
    passthrough : tuple[type[Exception], ...]
        Errors raised on unlogged, for the caller to handle.

    Returns
    -------
    T | None
        The step's result, or None when it failed.
    """
    try:
        return step()
    except passthrough:
        raise
    except REFRESH_ERRORS:
        logger.exception('refresh failed')
    # Last resort at the process edge, so any crash reaches errors.jsonl.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception('refresh failed unexpectedly')
    return None


def finish_refresh(
    *, record: HandoffRecord, tail: RefreshTail, build: Callable[[], None],
) -> RefreshReport:
    """
    Build the run tables, then check and swap in a handed-over timetable.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail.
    build : Callable[[], None]
        Adds the run tables to record.scratch.

    Returns
    -------
    RefreshReport
        What the whole refresh did; the peak covers the load and the extras.

    Raises
    ------
    LockLostError
        When the refresh lock did not survive the exec.
    """
    layout = Layout(data_dir=tail.data_dir)
    if not holds_lock(fd=tail.lock_fd, path=layout.lock):
        raise LockLostError(f'refresh lock not held on fd {tail.lock_fd}')
    build()
    os.replace(record.scratch, record.out)
    check_timetable(path=record.out)
    swap_in(layout=layout, built=record.out, fetched=tail.fetched)
    return RefreshReport(
        downloaded=tail.fetched.downloaded,
        changed=tail.fetched.changed,
        rebuilt=True,
        seconds=round(time.monotonic() - tail.started, 3),
        max_rss_mb=max(record.peak_mb, max_rss_mb()),
    )


def complete_refresh(
    *, record: HandoffRecord, tail: RefreshTail, build: Callable[[], None],
) -> int:
    """
    Finish a handed-over refresh and print its report, logging failures.

    Work is cleared and the lock released whatever happens, so a failure
    leaves the old timetable, sources and states in place.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail.
    build : Callable[[], None]
        Adds the run tables to record.scratch.

    Returns
    -------
    int
        Exit status: 0 on success, 1 on failure.
    """
    configure_logging(error_file=tail.error_log)
    try:
        report = guarded(step=lambda: finish_refresh(
            record=record, tail=tail, build=build,
        ))
    finally:
        clear_work(work=Layout(data_dir=tail.data_dir).work)
        with suppress(OSError):
            os.close(tail.lock_fd)
    if report is None:
        return 1
    print(json.dumps(asdict(report)), flush=True)
    return 0


def complete(
    *, record: HandoffRecord, path: Path, build: Callable[[], None],
) -> int:
    """
    Finish the build or refresh a handoff file describes.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    path : Path
        Handoff file, removed once a plain build is done.
    build : Callable[[], None]
        Adds the run tables to record.scratch.

    Returns
    -------
    int
        Exit status.
    """
    if record.tail is not None:
        return complete_refresh(record=record, tail=record.tail, build=build)
    try:
        build()
        os.replace(record.scratch, record.out)
    finally:
        path.unlink(missing_ok=True)
    print(
        f'progress done dropped={record.dropped} partial={record.partial}',
        flush=True,
    )
    return 0
