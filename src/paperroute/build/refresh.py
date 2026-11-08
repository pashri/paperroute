"""Refresh the timetable from TfNSW and swap it in atomically."""

import fcntl
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from paperroute.adapters.tfnsw import BASE_URL
from paperroute.build.handoff import (
    HANDOFF_NAME,
    Fetched,
    Handoff,
    RefreshTail,
)
from paperroute.build.source_state import SourceState, load_states, save_states
from paperroute.build.sources import (
    SOURCES,
    SourceSpec,
    download,
    file_sha256,
    head,
    needs_download,
)
from paperroute.build.swap import (
    RefreshReport,
    check_timetable,
    clear_work,
    progress,
    swap_in,
)
from paperroute.build.timetable import (
    SCHEMA_VERSION,
    Window,
    build_timetable,
    yesterday,
)
from paperroute.city import SYDNEY, SYDNEY_REF
from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_device.rss import max_rss_mb
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.layout import Layout

TIMEOUT_S: Final[float] = 60
MIN_DAYS_LEFT: Final[int] = 7
BUILD_STARTS_PER_S: Final[float] = 1.0  # leaves the panel's share of 5/s
LIMITER: Final[SyncStartLimiter] = SyncStartLimiter(
    per_second=BUILD_STARTS_PER_S,
)


class RefreshBusy(Exception):
    """Another refresh holds the lock."""


@contextmanager
def refresh_lock(*, path: Path) -> Iterator[int]:
    """
    Hold an exclusive lock on a file.

    Parameters
    ----------
    path : Path
        Lock file, created if missing.

    Yields
    ------
    int
        The descriptor holding the lock, while it is held.

    Raises
    ------
    RefreshBusy
        If another process holds it.
    """
    with path.open('a') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RefreshBusy(str(path)) from error
        yield file.fileno()


def head_all(*, base_url: str, api_key: str) -> dict[str, SourceState]:
    """
    Read every source's validators.

    Parameters
    ----------
    base_url : str
        API root.
    api_key : str
        TfNSW API key.

    Returns
    -------
    dict[str, SourceState]
        Fresh validators by source name.
    """
    progress(phase='head')
    return {
        spec.name: head(
            base_url=base_url,
            spec=spec,
            api_key=api_key,
            timeout_s=TIMEOUT_S,
            limiter=LIMITER,
        )
        for spec in SOURCES
    }


def intact_sha(*, layout: Layout, name: str,
               stored: dict[str, SourceState]) -> str | None:
    """
    The stored SHA-256 of a source, if its zip on disk still matches it.

    Parameters
    ----------
    layout : Layout
        Data paths.
    name : str
        Source name.
    stored : dict[str, SourceState]
        Stored states.

    Returns
    -------
    str | None
        The digest, or None when there's no state, no zip, or the zip's
        bytes no longer hash to the stored digest.
    """
    path = layout.sources / f'{name}.zip'
    expected = stored[name].sha256 if name in stored else None
    if expected is None or not path.exists():
        return None
    return expected if file_sha256(path=path) == expected else None


def fetch_source(
    *,
    layout: Layout,
    spec: SourceSpec,
    base_url: str,
    api_key: str,
    fresh: SourceState,
    stored: SourceState | None,
    intact: str | None,
) -> tuple[SourceState, bool]:
    """
    Download one source into the scratch directory if its headers changed.

    Parameters
    ----------
    layout : Layout
        Data paths.
    spec : SourceSpec
        Source.
    base_url : str
        API root.
    api_key : str
        TfNSW API key.
    fresh : SourceState
        Validators from a new HEAD.
    stored : SourceState | None
        State saved after the last download.
    intact : str | None
        Digest of the stored zip if it is intact, else None.

    Returns
    -------
    tuple[SourceState, bool]
        The fresh validators with the new digest if downloaded, else the
        stored one; and whether it was downloaded.
    """
    if not needs_download(
        stored=stored,
        fresh=fresh,
        zip_exists=intact is not None,
    ):
        return replace(fresh, sha256=intact), False
    progress(phase=f'download {spec.name}')
    digest = download(
        base_url=base_url,
        spec=spec,
        api_key=api_key,
        out=layout.work / f'{spec.name}.zip',
        timeout_s=TIMEOUT_S,
        limiter=LIMITER,
    )
    return replace(fresh, sha256=digest), True


def download_changed(
    *,
    layout: Layout,
    base_url: str,
    api_key: str,
    fresh: dict[str, SourceState],
) -> Fetched:
    """
    Download the sources whose headers changed into the scratch directory.

    A source counts as changed when its new digest differs from the
    stored one, or its stored zip is missing or no longer matches.

    Parameters
    ----------
    layout : Layout
        Data paths.
    base_url : str
        API root.
    api_key : str
        TfNSW API key.
    fresh : dict[str, SourceState]
        Validators from head_all.

    Returns
    -------
    Fetched
        New states, and which sources were downloaded and which changed.
    """
    stored = load_states(path=layout.states)
    intact = {
        spec.name: intact_sha(layout=layout, name=spec.name, stored=stored)
        for spec in SOURCES
    }
    results = {
        spec.name: fetch_source(
            layout=layout,
            spec=spec,
            base_url=base_url,
            api_key=api_key,
            fresh=fresh[spec.name],
            stored=stored.get(spec.name),
            intact=intact[spec.name],
        )
        for spec in SOURCES
    }
    downloaded = tuple(name for name, (_, got) in results.items() if got)
    return Fetched(
        states={name: state for name, (state, _) in results.items()},
        downloaded=downloaded,
        changed=tuple(
            name
            for name in downloaded
            if results[name][0].sha256 != intact[name]
        ),
    )


def build_and_check(*, layout: Layout, changed: tuple[str, ...],
                    days: int, handoff: Handoff | None) -> Path:
    """
    Build a timetable in the scratch directory and check it.

    Parameters
    ----------
    layout : Layout
        Data paths.
    changed : tuple[str, ...]
        Sources to take from the scratch directory; the rest come from
        the stored zips.
    days : int
        Days the timetable covers, from yesterday in Sydney.
    handoff : Handoff | None
        When set, the build execs into the extras program, which checks
        and swaps in, and this never returns.

    Returns
    -------
    Path
        The checked timetable, still in the scratch directory.
    """
    progress(phase='build')
    out = layout.work / 'timetable.db'
    build_timetable(
        sources=[
            Source(
                name=spec.name,
                archive=(
                    layout.work if spec.name in changed else layout.sources
                ) / f'{spec.name}.zip',
            )
            for spec in SOURCES
        ],
        window=Window(start=yesterday(zone=SYDNEY.zone), days=days),
        city_ref=SYDNEY_REF,
        out=out,
        handoff=handoff,
    )
    check_timetable(path=out)
    return out


def read_meta(*, path: Path) -> dict[str, str]:
    """
    Read a timetable's meta table.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    dict[str, str]
        Meta keys and values, or {} when the file can't be read.
    """
    try:
        with closing(
            sqlite3.connect(f'file:{path}?mode=ro', uri=True),
        ) as conn:
            return dict(conn.execute('SELECT key, value FROM meta'))
    except sqlite3.Error:
        return {}


def days_left(*, meta: dict[str, str]) -> int:
    """
    Count the days from today in Sydney to the end of a timetable window.

    Parameters
    ----------
    meta : dict[str, str]
        The timetable's meta, from read_meta.

    Returns
    -------
    int
        Days left, or 0 when the meta has no window.
    """
    if 'window_start' not in meta or 'window_days' not in meta:
        return 0
    end = date.fromisoformat(meta['window_start']) + timedelta(
        days=int(meta['window_days']),
    )
    return (end - yesterday(zone=SYDNEY.zone)).days - 1


def update(*, layout: Layout, fetched: Fetched, days: int,
           force: bool, handoff: Handoff | None = None) -> bool:
    """
    Rebuild and swap in when needed, else just store the new validators.

    Parameters
    ----------
    layout : Layout
        Data paths.
    fetched : Fetched
        Outcome of download_changed.
    days : int
        Days the timetable covers.
    force : bool
        Rebuild even when nothing changed.
    handoff : Handoff | None
        Hand the rebuild over to the extras program; see build_and_check.

    Returns
    -------
    bool
        Whether the timetable was rebuilt. It is rebuilt when a source
        changed, when forced, when there is no timetable or fewer than
        MIN_DAYS_LEFT days remain in its window, or when it was built with
        another SCHEMA_VERSION or records another projection origin.
    """
    meta = read_meta(path=layout.timetable)
    origin = meta.get('origin')
    stale = (
        days_left(meta=meta) < MIN_DAYS_LEFT
        or meta.get('schema_version') != SCHEMA_VERSION
        or (origin is not None and origin != SYDNEY.projection.origin_text)
    )
    if not (fetched.changed or force or stale):
        save_states(path=layout.states, states=fetched.states)
        return False
    built = build_and_check(layout=layout, changed=fetched.changed, days=days,
                            handoff=handoff)
    swap_in(layout=layout, built=built, fetched=fetched)
    return True


def fresh_work(*, layout: Layout) -> None:
    """
    Clear the work folder and make sure the sources folder is there.

    Parameters
    ----------
    layout : Layout
        Where the refresh keeps its files.
    """
    clear_work(work=layout.work)
    layout.sources.mkdir(exist_ok=True)


def refresh(
    *,
    data_dir: Path,
    api_key: str,
    base_url: str = BASE_URL,
    days: int = 14,
    force: bool = False,
    error_log: Path | None = None,
    hand_over: bool = False,
) -> RefreshReport:
    """
    Fetch changed sources and rebuild the timetable if their bytes changed.

    Any failure before the swap leaves timetable.db, sources/ and the
    stored states as they were.

    Parameters
    ----------
    data_dir : Path
        Holds timetable.db, sources/, work/ and refresh.lock.
    api_key : str
        TfNSW API key.
    base_url : str
        API root, ending in '/'.
    days : int
        Days the timetable covers, from yesterday in Sydney.
    force : bool
        Rebuild even when no source changed.
    error_log : Path | None
        The errors.jsonl file inside data_dir, for the extras program
        after a hand over.
    hand_over : bool
        Exec into the extras program after the load, so the two never
        share memory; a rebuild then never returns here, and the extras
        program checks, swaps in, clears work/ and prints the report.

    Returns
    -------
    RefreshReport
        What was downloaded, changed and rebuilt.
    """
    started = time.monotonic()
    layout = Layout(data_dir=data_dir)
    with refresh_lock(path=layout.lock) as lock_fd:
        fresh_work(layout=layout)
        try:
            fetched = download_changed(
                layout=layout,
                base_url=base_url,
                api_key=api_key,
                fresh=head_all(base_url=base_url, api_key=api_key),
            )
            tail = RefreshTail(
                data_dir=data_dir, fetched=fetched, started=started,
                error_log=error_log, lock_fd=lock_fd,
            )
            rebuilt = update(
                layout=layout, fetched=fetched, days=days, force=force,
                handoff=(
                    Handoff(path=layout.work / HANDOFF_NAME, tail=tail)
                    if hand_over
                    else None
                ),
            )
        finally:
            clear_work(work=layout.work)
    return RefreshReport(
        downloaded=fetched.downloaded,
        changed=fetched.changed,
        rebuilt=rebuilt,
        seconds=round(time.monotonic() - started, 3),
        max_rss_mb=max_rss_mb(),
    )
