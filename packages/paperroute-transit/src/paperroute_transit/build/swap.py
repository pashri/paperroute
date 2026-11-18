"""Check a built timetable, swap it in and finish a refresh."""

import fcntl
import json
import logging
import os
import shutil
import sqlite3
import time
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from paperroute_device.logs import configure_logging
from paperroute_device.rss import max_rss_mb
from paperroute_transit.build.handoff import (
    Fetched,
    HandoffError,
    HandoffRecord,
    RefreshTail,
    hand_back,
    read_handoff,
)
from paperroute_transit.build.layout import Layout
from paperroute_transit.build.source_state import (
    SourceError,
    fsync_dir,
    load_states,
    rule_file,
    save_states,
)

logger = logging.getLogger(__name__)

MIN_TRIPS: Final[int] = 1000


class TimetableCheckError(Exception):
    """A newly built timetable failed its integrity check."""


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
    failed: tuple[str, ...]
    all_failed: bool
    rebuilt: bool
    seconds: float
    max_rss_mb: float


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


def check_timetable(*, path: Path, optional: tuple[str, ...] = ()) -> None:
    """
    Check a built timetable before it goes live.

    Parameters
    ----------
    path : Path
        Built timetable.
    optional : tuple[str, ...]
        Sources whose trips do not count towards the floor.

    Raises
    ------
    TimetableCheckError
        If SQLite's integrity check fails or it has under MIN_TRIPS trips
        from its required sources.
    """
    progress(phase='check')
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        (verdict,) = conn.execute('PRAGMA integrity_check').fetchone()
        (trips,) = conn.execute(
            'SELECT count(*) FROM trips'
            ' WHERE source NOT IN (SELECT value FROM json_each(?))',
            (json.dumps(list(optional)),),
        ).fetchone()
    if verdict != 'ok':
        raise TimetableCheckError(f'integrity_check: {verdict}')
    if trips < MIN_TRIPS:
        raise TimetableCheckError(f'{trips} trips, want {MIN_TRIPS}')


def store_rules(*, layout: Layout, fetched: Fetched) -> None:
    """
    Store the rule files of the cuts just made; drop those of forgotten ones.

    Parameters
    ----------
    layout : Layout
        Data paths.
    fetched : Fetched
        Downloads to store.
    """
    for name in fetched.downloaded:
        staged = rule_file(directory=layout.work, name=name)
        if staged.exists():
            os.replace(staged, rule_file(directory=layout.sources, name=name))
    for name in load_states(path=layout.states):
        if name not in fetched.states:
            rule_file(directory=layout.sources, name=name).unlink(
                missing_ok=True,
            )


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
    store_rules(layout=layout, fetched=fetched)
    fsync_dir(path=layout.sources)
    save_states(path=layout.states, states=fetched.states)


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


def check_scratch(*, record: HandoffRecord, scratch: Path) -> None:
    """
    Check the handoff is for the scratch file the exec was given.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    scratch : Path
        Scratch file from the command line.

    Raises
    ------
    HandoffError
        When they differ.
    """
    if record.scratch != scratch:
        raise HandoffError(f'handoff is for {record.scratch}, not {scratch}')


def prepare_refresh(
    *, record: HandoffRecord, tail: RefreshTail, scratch: Path,
    held: bool, build: Callable[[Path], None],
) -> bool:
    """
    Build the run tables and check a handed-over timetable, before any swap.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail.
    scratch : Path
        Scratch file from the command line.
    held : bool
        Whether the refresh lock survived the exec.
    build : Callable[[Path], None]
        Adds the run tables to a scratch file.

    Returns
    -------
    bool
        Always True.

    Raises
    ------
    LockLostError
        When the refresh lock did not survive the exec.
    """
    if not held:
        raise LockLostError(f'refresh lock not held on fd {tail.lock_fd}')
    check_scratch(record=record, scratch=scratch)
    build(record.scratch)
    os.replace(record.scratch, record.out)
    check_timetable(path=record.out, optional=tail.fetched.optional)
    return True


def finish_refresh(
    *, record: HandoffRecord, tail: RefreshTail,
) -> RefreshReport:
    """
    Swap a checked, handed-over timetable in and report the refresh.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail.

    Returns
    -------
    RefreshReport
        What the whole refresh did; the peak covers the load and the extras.
    """
    swap_in(layout=Layout(data_dir=tail.data_dir), built=record.out,
            fetched=tail.fetched)
    return RefreshReport(
        downloaded=tail.fetched.downloaded,
        changed=tail.fetched.changed,
        failed=tail.fetched.failed,
        all_failed=tail.fetched.all_failed,
        rebuilt=True,
        seconds=round(time.monotonic() - tail.started, 3),
        max_rss_mb=max(record.peak_mb, max_rss_mb()),
    )


def printed(*, report: RefreshReport | None) -> int:
    """
    Print a refresh's report as one JSON line.

    Parameters
    ----------
    report : RefreshReport | None
        The report; None when the refresh failed.

    Returns
    -------
    int
        The exit code: 0 once printed, 1 with no report or when every
        source failed.
    """
    if report is None:
        return 1
    print(json.dumps(asdict(report)), flush=True)
    return 1 if report.all_failed else 0


def complete_refresh(
    *, record: HandoffRecord, tail: RefreshTail, scratch: Path,
    build: Callable[[Path], None], path: Path, city_ref: str,
) -> int:
    """
    Finish a handed-over refresh and print its report, logging failures.

    Work is cleared whatever happens, so a failure leaves the old
    timetable, sources and states in place. The lock descriptor is closed
    only when it was checked to be the lock: otherwise its number may
    belong to another file. A failure before the swap, with the lock held
    and an attempt left, hands back to the retry program instead, keeping
    work and the lock.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail.
    scratch : Path
        Scratch file from the command line.
    build : Callable[[Path], None]
        Adds the run tables to a scratch file.
    path : Path
        Handoff file, for the retry program.
    city_ref : str
        The city's reference, for the retry program.

    Returns
    -------
    int
        Exit status: 0 on success, 1 on failure.
    """
    layout = Layout(data_dir=tail.data_dir)
    held = holds_lock(fd=tail.lock_fd, path=layout.lock)
    configure_logging(error_file=tail.error_log)
    report = None
    retry = False
    try:
        ready = guarded(step=lambda: prepare_refresh(
            record=record, tail=tail, scratch=scratch, held=held,
            build=build,
        ))
        retry = not ready and held and bool(tail.fallbacks)
        if ready:
            report = guarded(
                step=lambda: finish_refresh(record=record, tail=tail),
            )
    finally:
        if not retry:
            clear_work(work=layout.work)
            if held:
                os.close(tail.lock_fd)
    if retry:
        hand_back(path=path, city_ref=city_ref, lock_fd=tail.lock_fd)
    return printed(report=report)


def complete_build(
    *, record: HandoffRecord, path: Path, scratch: Path,
    build: Callable[[Path], None],
) -> int:
    """
    Finish a handed-over plain build and print the merge line.

    Parameters
    ----------
    record : HandoffRecord
        The handoff, with no refresh tail.
    path : Path
        Handoff file, removed whatever happens.
    scratch : Path
        Scratch file from the command line.
    build : Callable[[Path], None]
        Adds the run tables to a scratch file.

    Returns
    -------
    int
        Exit status.
    """
    try:
        check_scratch(record=record, scratch=scratch)
        build(record.scratch)
        os.replace(record.scratch, record.out)
    finally:
        path.unlink(missing_ok=True)
    print(
        f'progress done dropped={record.dropped} partial={record.partial}'
        f' overnight={record.overnight}',
        flush=True,
    )
    return 0


def readable(*, path: Path) -> HandoffRecord | None:
    """
    Read a handoff file, logging to stderr when it can't be.

    Parameters
    ----------
    path : Path
        Handoff file.

    Returns
    -------
    HandoffRecord | None
        The record, or None when the file is unreadable.
    """
    try:
        return read_handoff(path=path)
    except (OSError, ValueError):
        configure_logging()
        logger.exception('handoff unreadable')
        return None


def land(
    *, scratch: Path, path: Path, build: Callable[[Path], None],
    city_ref: str,
) -> int:
    """
    Read a handoff file and finish the build or refresh it describes.

    An unreadable file is logged to stderr only: with no valid record
    there is no trusted error log or work dir, and the next refresh clears
    work/ when it starts.

    Parameters
    ----------
    scratch : Path
        Scratch file from the command line.
    path : Path
        Handoff file.
    build : Callable[[Path], None]
        Adds the run tables to a scratch file.
    city_ref : str
        The city's reference, for the retry program.

    Returns
    -------
    int
        Exit status.
    """
    record = readable(path=path)
    if record is None:
        return 1
    if record.tail is not None:
        return complete_refresh(
            record=record, tail=record.tail, scratch=scratch, build=build,
            path=path, city_ref=city_ref,
        )
    return complete_build(
        record=record, path=path, scratch=scratch, build=build,
    )
