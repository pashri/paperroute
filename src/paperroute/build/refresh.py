"""Refresh the timetable from TfNSW and swap it in atomically."""

import fcntl
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from paperroute.adapters.tfnsw import BASE_URL
from paperroute.build.sources import (
    SOURCES,
    SourceSpec,
    SourceState,
    download,
    file_sha256,
    fsync_dir,
    head,
    load_states,
    needs_download,
    save_states,
)
from paperroute.build.timetable import (
    SCHEMA_VERSION,
    Source,
    Window,
    build_timetable,
    yesterday,
)
from paperroute.observers import max_rss_mb

MIN_TRIPS: Final[int] = 1000
TIMEOUT_S: Final[float] = 60
MIN_DAYS_LEFT: Final[int] = 7


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


class RefreshBusy(Exception):
    """Another refresh holds the lock."""


class TimetableCheckError(Exception):
    """A newly built timetable failed its integrity check."""


def progress(*, phase: str) -> None:
    """
    Print a progress line.

    Parameters
    ----------
    phase : str
        What the refresh is doing.
    """
    print(f'progress {phase}', flush=True)


@contextmanager
def refresh_lock(*, path: Path) -> Iterator[None]:
    """
    Hold an exclusive lock on a file.

    Parameters
    ----------
    path : Path
        Lock file, created if missing.

    Yields
    ------
    None
        While the lock is held.

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
        yield


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


def build_and_check(*, layout: Layout, changed: tuple[str, ...],
                    days: int) -> Path:
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
        window=Window(start=yesterday(), days=days),
        out=out,
    )
    check_timetable(path=out)
    return out


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


def days_left(*, path: Path) -> int:
    """
    Count the days from today in Sydney to the end of a timetable window.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    int
        Days left, or 0 when the file or its window can't be read.
    """
    meta = read_meta(path=path)
    if 'window_start' not in meta or 'window_days' not in meta:
        return 0
    end = date.fromisoformat(meta['window_start']) + timedelta(
        days=int(meta['window_days']),
    )
    return (end - yesterday()).days - 1


def update(*, layout: Layout, fetched: Fetched, days: int,
           force: bool) -> bool:
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

    Returns
    -------
    bool
        Whether the timetable was rebuilt. It is rebuilt when a source
        changed, when forced, when there is no timetable or fewer than
        MIN_DAYS_LEFT days remain in its window, or when it was built with
        another SCHEMA_VERSION.
    """
    stale = (
        days_left(path=layout.timetable) < MIN_DAYS_LEFT
        or read_meta(path=layout.timetable).get('schema_version')
        != SCHEMA_VERSION
    )
    if not (fetched.changed or force or stale):
        save_states(path=layout.states, states=fetched.states)
        return False
    built = build_and_check(layout=layout, changed=fetched.changed, days=days)
    swap_in(layout=layout, built=built, fetched=fetched)
    return True


def refresh(
    *,
    data_dir: Path,
    api_key: str,
    base_url: str = BASE_URL,
    days: int = 14,
    force: bool = False,
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

    Returns
    -------
    RefreshReport
        What was downloaded, changed and rebuilt.
    """
    started = time.monotonic()
    layout = Layout(data_dir=data_dir)
    with refresh_lock(path=data_dir / 'refresh.lock'):
        clear_work(work=layout.work)
        layout.sources.mkdir(exist_ok=True)
        try:
            fetched = download_changed(
                layout=layout,
                base_url=base_url,
                api_key=api_key,
                fresh=head_all(base_url=base_url, api_key=api_key),
            )
            rebuilt = update(layout=layout, fetched=fetched, days=days,
                             force=force)
        finally:
            clear_work(work=layout.work)
    return RefreshReport(
        downloaded=fetched.downloaded,
        changed=fetched.changed,
        rebuilt=rebuilt,
        seconds=round(time.monotonic() - started, 3),
        max_rss_mb=max_rss_mb(),
    )
