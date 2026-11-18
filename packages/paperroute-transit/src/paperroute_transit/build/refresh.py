"""Refresh the timetable from the city's sources and swap it in."""

import fcntl
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import date, timedelta, tzinfo
from pathlib import Path
from typing import Final

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_device.rss import max_rss_mb
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.handoff import (
    HANDOFF_NAME,
    Fetched,
    Handoff,
    RefreshTail,
)
from paperroute_transit.build.layout import Layout
from paperroute_transit.build.source_state import (
    SourceError,
    SourceState,
    load_states,
    save_states,
)
from paperroute_transit.build.sources import (
    BuildParts,
    Downloader,
    SourceCut,
    SourceSpec,
    file_sha256,
    needs_download,
)
from paperroute_transit.build.swap import (
    RefreshReport,
    check_timetable,
    clear_work,
    progress,
    swap_in,
)
from paperroute_transit.build.timetable import (
    SCHEMA_VERSION,
    Window,
    build_timetable,
    yesterday,
)
from paperroute_transit.core.city import City

logger = logging.getLogger(__name__)

TIMEOUT_S: Final[float] = 60
MIN_DAYS_LEFT: Final[int] = 7
DOWNLOAD_SUFFIX: Final[str] = '.download.zip'
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


def head_source(
    *,
    spec: SourceSpec,
    downloader: Downloader,
) -> SourceState | None:
    """
    Read one source's validators, logging a failure.

    Parameters
    ----------
    spec : SourceSpec
        Source.
    downloader : Downloader
        Asks for it.

    Returns
    -------
    SourceState | None
        Fresh validators, or None when the HEAD failed.
    """
    try:
        return downloader.head(spec=spec, timeout_s=TIMEOUT_S, limiter=LIMITER)
    except SourceError:
        logger.exception('%s: HEAD failed, using the stored zip', spec.name)
        return None


def head_all(*, parts: BuildParts) -> dict[str, SourceState | None]:
    """
    Read every source's validators.

    Parameters
    ----------
    parts : BuildParts
        The city's sources and the downloader that asks for them.

    Returns
    -------
    dict[str, SourceState | None]
        Fresh validators by source name, in the sources' order; None for
        a source whose HEAD failed.
    """
    progress(phase='head')
    return {
        spec.name: head_source(spec=spec, downloader=parts.downloader)
        for spec in parts.sources
    }


@dataclass(frozen=True)
class SourceOutcome:
    """A source's state after a refresh, and how it got there."""

    state: SourceState
    downloaded: bool
    failed: bool = False


def stored_copy(
    *,
    name: str,
    stored: SourceState | None,
    intact: str | None,
) -> SourceOutcome:
    """
    Fall back on a source's stored zip after its HEAD or download failed.

    Parameters
    ----------
    name : str
        Source name.
    stored : SourceState | None
        State saved after the last download.
    intact : str | None
        Digest of the stored zip if it is intact, else None.

    Returns
    -------
    SourceOutcome
        The stored state, marked failed, so the next run asks again.

    Raises
    ------
    SourceError
        When there is no intact stored zip to fall back on.
    """
    if stored is None or intact is None:
        raise SourceError(f'{name}: failed, with no stored zip to use')
    return SourceOutcome(state=stored, downloaded=False, failed=True)


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


def cut_into(
    *,
    name: str,
    cut: SourceCut,
    download: Path,
    out: Path,
) -> str:
    """
    Cut a downloaded zip into the scratch directory and delete it.

    Parameters
    ----------
    name : str
        Source name.
    cut : SourceCut
        The city's cut.
    download : Path
        The downloaded zip, deleted afterwards.
    out : Path
        Where the cut goes.

    Returns
    -------
    str
        The cut's SHA-256.

    Raises
    ------
    SourceError
        If the cut fails.
    """
    try:
        cut(archive=download, out=out)
        return file_sha256(path=out)
    except Exception as error:
        raise SourceError(
            f'{name}: cut failed, {type(error).__name__}',
        ) from error
    finally:
        download.unlink(missing_ok=True)


def fetched_zip(
    *,
    layout: Layout,
    spec: SourceSpec,
    downloader: Downloader,
) -> str:
    """
    Download one source, cutting it if its city does, into the scratch dir.

    Parameters
    ----------
    layout : Layout
        Data paths.
    spec : SourceSpec
        Source.
    downloader : Downloader
        Fetches the zip.

    Returns
    -------
    str
        The SHA-256 of the zip left in the scratch directory.
    """
    kept = layout.work / f'{spec.name}.zip'
    if spec.cut is None:
        return downloader.download(
            spec=spec,
            out=kept,
            timeout_s=TIMEOUT_S,
            limiter=LIMITER,
        )
    download = layout.work / f'{spec.name}{DOWNLOAD_SUFFIX}'
    downloader.download(
        spec=spec,
        out=download,
        timeout_s=TIMEOUT_S,
        limiter=LIMITER,
    )
    return cut_into(name=spec.name, cut=spec.cut, download=download, out=kept)


def fetch_source(
    *,
    layout: Layout,
    spec: SourceSpec,
    downloader: Downloader,
    fresh: SourceState | None,
    stored: SourceState | None,
    intact: str | None,
) -> SourceOutcome:
    """
    Download one source into the scratch directory if its headers changed.

    A failed HEAD or download falls back on the stored zip.

    Parameters
    ----------
    layout : Layout
        Data paths.
    spec : SourceSpec
        Source.
    downloader : Downloader
        Fetches the zip.
    fresh : SourceState | None
        Validators from a new HEAD; None when it failed.
    stored : SourceState | None
        State saved after the last download.
    intact : str | None
        Digest of the stored zip if it is intact, else None.

    Returns
    -------
    SourceOutcome
        The fresh validators with the new digest if downloaded, else the
        stored one; or the stored state when the source failed.
    """
    if fresh is None:
        return stored_copy(name=spec.name, stored=stored, intact=intact)
    if not needs_download(
        stored=stored,
        fresh=fresh,
        zip_exists=intact is not None,
    ):
        return SourceOutcome(
            state=replace(fresh, sha256=intact),
            downloaded=False,
        )
    progress(phase=f'download {spec.name}')
    try:
        digest = fetched_zip(layout=layout, spec=spec, downloader=downloader)
    except SourceError:
        logger.exception(
            '%s: download failed, using the stored zip',
            spec.name,
        )
        return stored_copy(name=spec.name, stored=stored, intact=intact)
    return SourceOutcome(state=replace(fresh, sha256=digest), downloaded=True)


def outcome_of(
    *,
    layout: Layout,
    spec: SourceSpec,
    downloader: Downloader,
    fresh: SourceState | None,
    stored: SourceState | None,
    intact: str | None,
) -> SourceOutcome | None:
    """
    Fetch one source, leaving an optional one out if it has no copy.

    Parameters
    ----------
    layout : Layout
        Data paths.
    spec : SourceSpec
        Source.
    downloader : Downloader
        Fetches the zip.
    fresh : SourceState | None
        Validators from a new HEAD; None when it failed.
    stored : SourceState | None
        State saved after the last download.
    intact : str | None
        Digest of the stored zip if it is intact, else None.

    Returns
    -------
    SourceOutcome | None
        The outcome, or None for an optional source with no copy.

    Raises
    ------
    SourceError
        When a required source has no copy to build from.
    """
    try:
        return fetch_source(
            layout=layout,
            spec=spec,
            downloader=downloader,
            fresh=fresh,
            stored=stored,
            intact=intact,
        )
    except SourceError:
        if not spec.optional:
            raise
        logger.warning(
            '%s: no copy to build from, building without it',
            spec.name,
        )
        return None


def download_changed(
    *,
    layout: Layout,
    parts: BuildParts,
    fresh: dict[str, SourceState | None],
) -> Fetched:
    """
    Download the sources whose headers changed into the scratch directory.

    A source counts as changed when its new digest differs from the
    stored one, or its stored zip is missing or no longer matches. A
    source whose HEAD or download failed keeps its stored zip and state.

    Parameters
    ----------
    layout : Layout
        Data paths.
    parts : BuildParts
        The city's sources and their downloader.
    fresh : dict[str, SourceState | None]
        Validators from head_all.

    Returns
    -------
    Fetched
        New states, and which sources were downloaded, changed and failed.
    """
    stored = load_states(path=layout.states)
    intact = {
        spec.name: intact_sha(layout=layout, name=spec.name, stored=stored)
        for spec in parts.sources
    }
    outcomes = {
        spec.name: outcome_of(
            layout=layout,
            spec=spec,
            downloader=parts.downloader,
            fresh=fresh[spec.name],
            stored=stored.get(spec.name),
            intact=intact[spec.name],
        )
        for spec in parts.sources
    }
    results = {
        name: outcome
        for name, outcome in outcomes.items()
        if outcome is not None
    }
    downloaded = tuple(
        name
        for name, outcome in results.items()
        if outcome.downloaded
    )
    return Fetched(
        states={name: outcome.state for name, outcome in results.items()},
        downloaded=downloaded,
        changed=tuple(
            name
            for name in downloaded
            if results[name].state.sha256 != intact[name]
        ),
        failed=tuple(
            name
            for name, outcome in outcomes.items()
            if outcome is None or outcome.failed
        ),
        optional=tuple(spec.name for spec in parts.sources if spec.optional),
    )


def present_parts(*, parts: BuildParts, fetched: Fetched) -> BuildParts:
    """
    Keep the sources there is a copy of to build from.

    Parameters
    ----------
    parts : BuildParts
        The city's reference and its sources.
    fetched : Fetched
        Outcome of download_changed.

    Returns
    -------
    BuildParts
        The parts without any optional source that has no copy.
    """
    return replace(
        parts,
        sources=tuple(
            spec for spec in parts.sources if spec.name in fetched.states
        ),
    )


def build_and_check(
    *,
    layout: Layout,
    changed: tuple[str, ...],
    days: int,
    handoff: Handoff | None,
    city: City,
    parts: BuildParts,
) -> Path:
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
        Days the timetable covers, from yesterday in the city's zone.
    handoff : Handoff | None
        When set, the build execs into the extras program, which checks
        and swaps in, and this never returns.
    city : City
        The city, for its time zone.
    parts : BuildParts
        The city's reference and its sources, in the order they load.

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
                optional=spec.optional,
            )
            for spec in parts.sources
        ],
        window=Window(start=yesterday(zone=city.zone), days=days),
        city_ref=parts.city_ref,
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


def days_left(*, meta: dict[str, str], zone: tzinfo) -> int:
    """
    Count the days from today to the end of a timetable window.

    Parameters
    ----------
    meta : dict[str, str]
        The timetable's meta, from read_meta.
    zone : tzinfo
        The city's time zone, which says what today is.

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
    return (end - yesterday(zone=zone)).days - 1


def without_new_copies(*, layout: Layout, fetched: Fetched) -> Fetched | None:
    """
    Drop the new copy of every optional source that was just downloaded.

    A source with an intact stored zip goes back to its stored state; one
    without is left out. Each is marked failed.

    Parameters
    ----------
    layout : Layout
        Data paths.
    fetched : Fetched
        Outcome of download_changed.

    Returns
    -------
    Fetched | None
        The outcome without the new copies, or None when no optional
        source has one.
    """
    dropped = tuple(
        name for name in fetched.changed if name in fetched.optional
    )
    if not dropped:
        return None
    stored = load_states(path=layout.states)
    states = {
        name: state
        for name, state in fetched.states.items()
        if name not in dropped
    }
    for name in dropped:
        if intact_sha(layout=layout, name=name, stored=stored) is not None:
            states[name] = stored[name]
    return replace(
        fetched,
        states=states,
        downloaded=tuple(n for n in fetched.downloaded if n not in dropped),
        changed=tuple(n for n in fetched.changed if n not in dropped),
        failed=(*fetched.failed, *dropped),
    )


def build_or_fall_back(
    *,
    layout: Layout,
    fetched: Fetched,
    days: int,
    handoff: Handoff | None,
    city: City,
    parts: BuildParts,
) -> tuple[Path, Fetched]:
    """
    Build the timetable, again without any new optional copy if that fails.

    Parameters
    ----------
    layout : Layout
        Data paths.
    fetched : Fetched
        Outcome of download_changed.
    days : int
        Days the timetable covers.
    handoff : Handoff | None
        Hand the rebuild over to the extras program; see build_and_check.
    city : City
        The city, for its time zone.
    parts : BuildParts
        The city's reference and its sources.

    Returns
    -------
    tuple[Path, Fetched]
        The checked timetable and the outcome it was built from.

    Raises
    ------
    Exception
        The build's own, when no optional source's new copy was in it.
    """
    try:
        built = build_and_check(
            layout=layout, changed=fetched.changed, days=days,
            handoff=handoff, city=city,
            parts=present_parts(parts=parts, fetched=fetched),
        )
    except Exception:
        fallback = without_new_copies(layout=layout, fetched=fetched)
        if fallback is not None:
            dropped = set(fetched.changed) - set(fallback.changed)
            logger.exception(
                'build failed, building again without the new %s',
                ', '.join(sorted(dropped)),
            )
            return build_and_check(
                layout=layout, changed=fallback.changed, days=days,
                handoff=handed(handoff=handoff, fetched=fallback), city=city,
                parts=present_parts(parts=parts, fetched=fallback),
            ), fallback
        raise
    return built, fetched


def handed(*, handoff: Handoff | None, fetched: Fetched) -> Handoff | None:
    """
    Point a handoff's refresh tail at another outcome.

    Parameters
    ----------
    handoff : Handoff | None
        The handoff, if any.
    fetched : Fetched
        The outcome the tail should carry.

    Returns
    -------
    Handoff | None
        The handoff with its tail's outcome replaced, or as it was when it
        has no tail.
    """
    if handoff is None or handoff.tail is None:
        return handoff
    return replace(handoff, tail=replace(handoff.tail, fetched=fetched))


def update(
    *,
    layout: Layout,
    fetched: Fetched,
    days: int,
    force: bool,
    city: City,
    parts: BuildParts,
    handoff: Handoff | None = None,
) -> tuple[bool, Fetched]:
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
    city : City
        The city, for its time zone and projection origin.
    parts : BuildParts
        The city's reference and its sources.
    handoff : Handoff | None
        Hand the rebuild over to the extras program; see build_and_check.

    Returns
    -------
    tuple[bool, Fetched]
        Whether the timetable was rebuilt, and the outcome stored. It is
        rebuilt when a source changed, when forced, when there is no
        timetable or fewer than MIN_DAYS_LEFT days remain in its window,
        or when it was built with another SCHEMA_VERSION or records
        another projection origin. The outcome differs from the one given
        when a build without an optional source's new copy was needed.
    """
    meta = read_meta(path=layout.timetable)
    origin = meta.get('origin')
    stale = (
        days_left(meta=meta, zone=city.zone) < MIN_DAYS_LEFT
        or meta.get('schema_version') != SCHEMA_VERSION
        or (origin is not None and origin != city.projection.origin_text)
    )
    if not (fetched.changed or force or stale):
        save_states(path=layout.states, states=fetched.states)
        return False, fetched
    built, fetched = build_or_fall_back(
        layout=layout,
        fetched=fetched,
        days=days,
        handoff=handoff,
        city=city,
        parts=parts,
    )
    swap_in(layout=layout, built=built, fetched=fetched)
    return True, fetched


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
    city: City,
    parts: BuildParts,
    days: int = 14,
    force: bool = False,
    error_log: Path | None = None,
    hand_over: bool = False,
) -> RefreshReport:
    """
    Fetch changed sources and rebuild the timetable if their bytes changed.

    A source whose HEAD, download or cut fails is logged and built from
    its stored zip. A failed required source with no stored zip fails the
    refresh and an optional one is left out. Any other failure before the
    swap leaves timetable.db, sources/ and the stored states as they
    were.

    Parameters
    ----------
    data_dir : Path
        Holds timetable.db, sources/, work/ and refresh.lock.
    city : City
        The city, for its time zone and projection origin.
    parts : BuildParts
        The city's reference, its sources and their downloader.
    days : int
        Days the timetable covers, from yesterday in the city's zone.
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
                parts=parts,
                fresh=head_all(parts=parts),
            )
            tail = RefreshTail(
                data_dir=data_dir, fetched=fetched, started=started,
                error_log=error_log, lock_fd=lock_fd,
            )
            rebuilt, fetched = update(
                layout=layout, fetched=fetched, days=days, force=force,
                city=city, parts=parts,
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
        failed=fetched.failed,
        all_failed=fetched.all_failed,
        rebuilt=rebuilt,
        seconds=round(time.monotonic() - started, 3),
        max_rss_mb=max_rss_mb(),
    )
