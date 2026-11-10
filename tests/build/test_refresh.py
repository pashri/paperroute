"""Tests for the nightly timetable refresh."""

import fcntl
import hashlib
import json
import os
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing
from datetime import date, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_sydney.build.tfnsw_sources import (
    SYDNEY_SOURCES,
    TfnswDownloader,
)
from paperroute_sydney.city import SYDNEY, SYDNEY_REF
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.kit_steps import step
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build import handoff as handoff_module
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from paperroute_transit.build.refresh import RefreshBusy, refresh
from paperroute_transit.build.source_state import SourceError, SourceState
from paperroute_transit.build.sources import BuildParts, SourceSpec
from paperroute_transit.build.swap import RefreshReport, TimetableCheckError
from paperroute_transit.core.banners import timetable_banner
from paperroute_transit.core.messages import TimetableSeen
from tests.build.feed_server import FeedServer, Served, feed_server
from tests.build.feeds import bus_feed, bus_files
from tests.core.test_state import fresh

REAL: Final[Path] = Path(__file__).parent.parent / 'fixtures' / 'real'
KEY: Final[str] = 'secret-key-0123456789'
METRO: Final[str] = '/v2/gtfs/schedule/metro'
DAYS: Final[int] = 9
START: Final[date] = date(2026, 10, 16)


def metro_zip(*, tmp_path: Path, comment: bytes = b'') -> bytes:
    """
    A small feed standing in for Metro.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.
    comment : bytes
        Zip comment, to change the bytes but not the contents.

    Returns
    -------
    bytes
        The zip file.
    """
    path = bus_feed(path=tmp_path / 'metro-src.zip')
    with zipfile.ZipFile(path, 'a') as archive:
        archive.comment = comment
    return path.read_bytes()


@pytest.fixture(name='server')
def _server(tmp_path: Path) -> Iterator[FeedServer]:
    """
    A feed server holding all three sources.

    Parameters
    ----------
    tmp_path : Path
        Directory for the Metro zip.

    Yields
    ------
    FeedServer
        Server, closed after the test.
    """
    bodies = {
        'sydneytrains': (REAL / 'sydneytrains.zip').read_bytes(),
        'metro': metro_zip(tmp_path=tmp_path),
        'bundle': (REAL / 'bundle.zip').read_bytes(),
    }
    with feed_server() as server:
        for spec in SYDNEY_SOURCES:
            server.state.files['/' + spec.path] = Served(
                body=bodies[spec.name],
            )
        yield server


@pytest.fixture(name='waits', autouse=True)
def _waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """
    Record when the refresh's requests start instead of sleeping.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Patcher.

    Returns
    -------
    list[float]
        When each request started, on a clock that moves only by sleeping.
    """
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append((waits[-1] if waits else 0.0) + seconds)

    limiter = SyncStartLimiter(
        per_second=1,
        clock=lambda: waits[-1] if waits else 0.0,
        sleep=sleep,
    )
    monkeypatch.setattr(refresh_module, 'LIMITER', limiter)
    return waits


@pytest.fixture(name='data_dir')
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    An empty data directory, a fixed window start and a low trip floor.

    Parameters
    ----------
    tmp_path : Path
        Test directory.
    monkeypatch : pytest.MonkeyPatch
        Patcher.

    Returns
    -------
    Path
        The data directory.
    """
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    monkeypatch.setattr(
        refresh_module,
        'yesterday',
        lambda *, zone: START,
    )
    path = tmp_path / 'data'
    path.mkdir()
    return path


def parts_for(*, server: FeedServer) -> BuildParts:
    """
    Sydney's build parts with a fake key, against the test server.

    Parameters
    ----------
    server : FeedServer
        Feed server.

    Returns
    -------
    BuildParts
        The parts.
    """
    return BuildParts(
        city_ref=SYDNEY_REF,
        sources=SYDNEY_SOURCES,
        downloader=TfnswDownloader(key=lambda: KEY, base_url=server.base_url),
    )


def run(
    *,
    server: FeedServer,
    data_dir: Path,
    force: bool = False,
) -> RefreshReport:
    """
    Refresh a two-day timetable against the test server.

    Parameters
    ----------
    server : FeedServer
        Feed server.
    data_dir : Path
        Data directory.
    force : bool
        Rebuild even when nothing changed.

    Returns
    -------
    RefreshReport
        The refresh's report.
    """
    return refresh(
        data_dir=data_dir,
        city=SYDNEY,
        parts=parts_for(server=server),
        days=DAYS,
        force=force,
    )


def snapshot(*, data_dir: Path) -> dict[str, bytes]:
    """
    The bytes of the timetable, the stored zips and the states.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    dict[str, bytes]
        Relative path to contents.
    """
    files = [data_dir / 'timetable.db', *(data_dir / 'sources').iterdir()]
    return {
        str(path.relative_to(data_dir)): path.read_bytes()
        for path in files
    }


def window_start(*, data_dir: Path) -> str:
    """
    The window start recorded in the live timetable.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    str
        The ISO date.
    """
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        (start,) = conn.execute(
            "SELECT value FROM meta WHERE key = 'window_start'",
        ).fetchone()
    return str(start)


def test_refresh_refuses_concurrent_run(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A held lock stops a second refresh before any request."""
    with (data_dir / 'refresh.lock').open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RefreshBusy):
            run(server=server, data_dir=data_dir)
    assert not server.state.hits


def test_first_refresh_downloads_all_and_builds(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """With nothing stored, every source is fetched and built."""
    report = run(server=server, data_dir=data_dir)
    assert report.rebuilt
    assert report.downloaded == ('sydneytrains', 'metro', 'bundle')
    assert report.changed == ('sydneytrains', 'metro', 'bundle')
    assert report.max_rss_mb > 0
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        (trips,) = conn.execute('SELECT count(*) FROM trips').fetchone()
    assert trips > 0
    states = json.loads(
        (data_dir / 'sources' / 'sources.json').read_text(encoding='utf-8'),
    )
    assert set(states) == {'sydneytrains', 'metro', 'bundle'}
    assert all(state['sha256'] for state in states.values())
    assert {path.name for path in (data_dir / 'sources').iterdir()} == {
        'sources.json',
        'sydneytrains.zip',
        'metro.zip',
        'bundle.zip',
    }


def test_every_request_waits_for_a_start_slot(
    server: FeedServer,
    data_dir: Path,
    waits: list[float],
) -> None:
    """Each HEAD and GET takes a slot a second after the one before."""
    run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3, 'GET': 3}
    assert waits == pytest.approx([1.01 * n for n in range(6)])


def test_unchanged_headers_skip_download(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A second run with the same headers only sends HEADs."""
    run(server=server, data_dir=data_dir)
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3}
    assert not report.rebuilt
    assert report.downloaded == ()


def test_new_etag_same_bytes_skips_rebuild(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A new ETag over the same bytes is downloaded but not rebuilt."""
    run(server=server, data_dir=data_dir)
    server.state.files[METRO].etag = '"2"'
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits['GET'] == 1
    assert report.downloaded == ('metro',)
    assert report.changed == ()
    assert not report.rebuilt
    states = json.loads(
        (data_dir / 'sources' / 'sources.json').read_text(encoding='utf-8'),
    )
    assert states['metro']['etag'] == '"2"'


def test_changed_bytes_rebuild(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """New bytes rebuild and swap in a new timetable file."""
    run(server=server, data_dir=data_dir)
    before = (data_dir / 'timetable.db').stat().st_ino
    server.state.files[METRO] = Served(
        body=metro_zip(tmp_path=tmp_path, comment=b'v2'),
        etag='"2"',
    )
    report = run(server=server, data_dir=data_dir)
    assert report.changed == ('metro',)
    assert report.rebuilt
    assert (data_dir / 'timetable.db').stat().st_ino != before
    assert (data_dir / 'sources' / 'metro.zip').read_bytes() == (
        server.state.files[METRO].body
    )


def test_force_rebuilds_without_changes(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """force rebuilds from the stored zips when nothing changed."""
    run(server=server, data_dir=data_dir)
    report = run(server=server, data_dir=data_dir, force=True)
    assert report.rebuilt
    assert report.changed == ()


def test_refresh_keeps_old_timetable_when_download_fails(
    server: FeedServer,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A download cut short keeps that source's stored zip and state."""
    run(server=server, data_dir=data_dir)
    before_ino = (data_dir / 'timetable.db').stat().st_ino
    before = snapshot(data_dir=data_dir)
    server.state.files[METRO].etag = '"2"'
    server.state.fail_midway.add(METRO)
    report = run(server=server, data_dir=data_dir)
    assert report.failed == ('metro',)
    assert not report.all_failed
    assert not report.rebuilt
    assert (data_dir / 'timetable.db').stat().st_ino == before_ino
    assert snapshot(data_dir=data_dir) == before
    assert 'metro: download failed' in caplog.text


def test_failed_head_keeps_the_stored_zip(
    server: FeedServer,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One source's failed HEAD is logged and the others still refresh."""
    run(server=server, data_dir=data_dir)
    before = snapshot(data_dir=data_dir)
    server.state.gone.add(METRO)
    report = run(server=server, data_dir=data_dir)
    assert report.failed == ('metro',)
    assert not report.all_failed
    assert not report.rebuilt
    assert snapshot(data_dir=data_dir) == before
    assert 'metro: HEAD failed' in caplog.text
    assert 'HTTP 404' in caplog.text


def test_failed_head_rebuilds_from_stored_zip_when_window_short(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed source's stored zip goes into a rebuild with fresh ones."""
    run(server=server, data_dir=data_dir)
    stored_metro = (data_dir / 'sources' / 'metro.zip').read_bytes()
    server.state.gone.add(METRO)
    bundle = '/' + SYDNEY_SOURCES[2].path
    server.state.files[bundle].etag = '"2"'
    later = START + timedelta(days=DAYS - 4)
    monkeypatch.setattr(refresh_module, 'yesterday', lambda *, zone: later)
    report = run(server=server, data_dir=data_dir)
    assert report.rebuilt
    assert report.failed == ('metro',)
    assert report.downloaded == ('bundle',)
    assert (data_dir / 'sources' / 'metro.zip').read_bytes() == stored_metro
    assert window_start(data_dir=data_dir) == later.isoformat()


def test_every_source_failing_still_rebuilds_when_window_short(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every source down, the stored zips still renew the window."""
    run(server=server, data_dir=data_dir)
    server.state.status = HTTPStatus.FORBIDDEN
    later = START + timedelta(days=DAYS - 4)
    monkeypatch.setattr(refresh_module, 'yesterday', lambda *, zone: later)
    report = run(server=server, data_dir=data_dir)
    assert report.rebuilt
    assert report.failed == ('sydneytrains', 'metro', 'bundle')
    assert report.all_failed
    assert window_start(data_dir=data_dir) == later.isoformat()


def stale_metro(*, tmp_path: Path, ends: date) -> bytes:
    """
    The Metro stand-in with a calendar that runs out on a given day.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.
    ends : date
        The calendar's last day.

    Returns
    -------
    bytes
        The zip file.
    """
    files = bus_files()
    files['calendar.txt'] = [
        {**row, 'end_date': ends.strftime('%Y%m%d')}
        for row in files['calendar.txt']
    ]
    path = write_feed(path=tmp_path / 'stale-metro.zip', files=files)
    return path.read_bytes()


def last_day(*, data_dir: Path) -> date | None:
    """
    The last day the board would read from the live timetable.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    date | None
        The repository's last day.
    """
    with closing(
        SqliteTimetableRepository(
            path=data_dir / 'timetable.db', city=SYDNEY,
        ),
    ) as repo:
        return repo.last_day()


def test_stale_source_brings_the_last_day_forward(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """A source whose calendar ends inside the window sets the last day."""
    ends = START + timedelta(days=4)
    server.state.files[METRO] = Served(
        body=stale_metro(tmp_path=tmp_path, ends=ends),
    )
    run(server=server, data_dir=data_dir)
    assert last_day(data_dir=data_dir) == ends


def test_rebuilds_from_stored_zips_keep_the_last_day(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renewing the window from stored zips never moves the last day on."""
    ends = START + timedelta(days=4)
    server.state.files[METRO] = Served(
        body=stale_metro(tmp_path=tmp_path, ends=ends),
    )
    run(server=server, data_dir=data_dir)
    server.state.status = HTTPStatus.FORBIDDEN
    for week in (1, 2, 3):
        later = START + timedelta(days=week * (DAYS - 4))
        monkeypatch.setattr(
            refresh_module, 'yesterday', lambda *, zone, day=later: day,
        )
        report = run(server=server, data_dir=data_dir)
        assert report.rebuilt
        assert report.all_failed
        assert window_start(data_dir=data_dir) == later.isoformat()
        assert last_day(data_dir=data_dir) == ends


def test_banner_shows_three_days_before_the_earliest_source_ends(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """The board warns from three days before a stale source runs out."""
    friday = date(2026, 10, 16)  # fresh()'s day in Sydney
    banners = []
    for ends in (friday + timedelta(days=3), friday + timedelta(days=2)):
        server.state.files[METRO] = Served(
            body=stale_metro(tmp_path=tmp_path, ends=ends),
            etag=f'"{ends}"',
        )
        run(server=server, data_dir=data_dir)
        state, _ = step(
            fresh(),
            TimetableSeen(version='v1', last_day=last_day(data_dir=data_dir)),
        )
        banners.append(timetable_banner(state=state))
    assert banners == [
        None,
        'Timetable ends Sunday. Check the internet and key.',
    ]


def test_failed_source_with_no_stored_zip_fails_the_refresh(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A source with neither a fresh nor a stored copy stops the build."""
    server.state.gone.add(METRO)
    with pytest.raises(SourceError, match='metro'):
        run(server=server, data_dir=data_dir)
    assert not (data_dir / 'timetable.db').exists()
    assert not (data_dir / 'sources' / 'sources.json').exists()


def test_failed_source_with_a_damaged_zip_fails_the_refresh(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A stored zip that no longer matches its digest is no fallback."""
    run(server=server, data_dir=data_dir)
    stored = data_dir / 'sources' / 'metro.zip'
    stored.write_bytes(stored.read_bytes()[:100])
    server.state.gone.add(METRO)
    with pytest.raises(SourceError, match='metro'):
        run(server=server, data_dir=data_dir)


def test_refresh_clears_stale_scratch(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """Leftovers from a killed run are removed before work starts."""
    work = data_dir / 'work'
    work.mkdir()
    (work / 'junk.zip').write_bytes(b'junk')
    (work / 'timetable.db').write_bytes(b'SQLite format 3\x00half')
    (work / 'stale').mkdir()
    run(server=server, data_dir=data_dir)
    assert work.is_dir()
    assert not list(work.iterdir())


def test_integrity_failure_keeps_old(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timetable below the trip floor is never swapped in."""
    run(server=server, data_dir=data_dir)
    before = snapshot(data_dir=data_dir)
    server.state.files[METRO] = Served(
        body=metro_zip(tmp_path=tmp_path, comment=b'v2'),
        etag='"2"',
    )
    monkeypatch.setattr(swap, 'MIN_TRIPS', 10**9)
    with pytest.raises(TimetableCheckError):
        run(server=server, data_dir=data_dir)
    assert snapshot(data_dir=data_dir) == before


def test_truncated_stored_zip_is_downloaded_again(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A stored zip that no longer matches its digest is replaced."""
    run(server=server, data_dir=data_dir)
    stored = data_dir / 'sources' / 'metro.zip'
    stored.write_bytes(stored.read_bytes()[:100])
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits['GET'] == 1
    assert report.downloaded == ('metro',)
    assert report.changed == ('metro',)
    assert report.rebuilt
    assert stored.read_bytes() == server.state.files[METRO].body


def test_window_running_out_rebuilds_from_stored_zips(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fewer than a week left in the window rebuilds with no download."""
    run(server=server, data_dir=data_dir)
    later = START + timedelta(days=DAYS - 4)
    monkeypatch.setattr(refresh_module, 'yesterday', lambda *, zone: later)
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3}
    assert report.rebuilt
    assert window_start(data_dir=data_dir) == later.isoformat()


def test_crash_before_states_saved_rebuilds_next_run(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dying after the swap but before the save heals on the next run."""
    run(server=server, data_dir=data_dir)
    server.state.files[METRO] = Served(
        body=metro_zip(tmp_path=tmp_path, comment=b'v2'),
        etag='"2"',
    )

    def crash(**_: object) -> None:
        raise OSError('power cut')

    with monkeypatch.context() as patch:
        patch.setattr(swap, 'save_states', crash)
        with pytest.raises(OSError, match='power cut'):
            run(server=server, data_dir=data_dir)
    report = run(server=server, data_dir=data_dir)
    assert report.rebuilt
    states = json.loads(
        (data_dir / 'sources' / 'sources.json').read_text(encoding='utf-8'),
    )
    for name, state in states.items():
        zip_bytes = (data_dir / 'sources' / f'{name}.zip').read_bytes()
        assert state['sha256'] == hashlib.sha256(zip_bytes).hexdigest()
    assert states['metro']['etag'] == '"2"'


def test_swap_fsyncs_sources_directory(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The renames into sources/ are flushed before the states are saved."""
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', fsync)
    run(server=server, data_dir=data_dir)
    assert (data_dir / 'sources').stat().st_ino in synced


def test_work_is_emptied_after_a_run(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A downloaded but unchanged zip doesn't stay in work/."""
    run(server=server, data_dir=data_dir)
    server.state.files[METRO].etag = '"2"'
    run(server=server, data_dir=data_dir)
    assert not list((data_dir / 'work').iterdir())


def test_refresh_rebuilds_when_schema_version_differs(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A timetable from older code is rebuilt with no source changed."""
    run(server=server, data_dir=data_dir)
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        conn.execute(
            "UPDATE meta SET value = '1' WHERE key = 'schema_version'",
        )
        conn.commit()
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3}
    assert report.changed == ()
    assert report.rebuilt


@pytest.mark.parametrize(
    ('origin', 'rebuilt'),
    [('0.0,51.5', True), ('151.2093,-33.8688', False), (None, False)],
)
def test_update_rebuilds_on_other_origin(
    server: FeedServer,
    data_dir: Path,
    origin: str | None,
    rebuilt: bool,
) -> None:
    """A current-schema timetable from another origin is rebuilt."""
    run(server=server, data_dir=data_dir)
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'origin'")
        if origin is not None:
            conn.execute(
                "INSERT INTO meta VALUES ('origin', ?)",
                (origin,),
            )
        conn.commit()
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3}
    assert report.changed == ()
    assert report.rebuilt is rebuilt


class RecordingDownloader:
    """A downloader that records the sources it is asked about."""

    def __init__(self) -> None:
        self.heads: list[str] = []
        self.failing: set[str] = set()

    def head(
        self,
        *,
        spec: SourceSpec,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> SourceState:
        """
        Record a HEAD and give fixed validators.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts.

        Returns
        -------
        SourceState
            Validators naming the source.

        Raises
        ------
        SourceError
            For a source in failing.
        """
        assert timeout_s > 0
        assert limiter is refresh_module.LIMITER
        self.heads.append(spec.name)
        if spec.name in self.failing:
            raise SourceError(f'{spec.name}: HTTP 404')
        return SourceState(etag=spec.name, last_modified=None, sha256=None)

    def download(
        self,
        *,
        spec: SourceSpec,
        out: Path,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> str:
        """
        Refuse to download.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        out : Path
            File to write.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts.

        Raises
        ------
        AssertionError
            Always; the HEAD test never downloads.
        """
        raise AssertionError(f'{spec.name} {out} {timeout_s} {limiter}')


def test_head_all_asks_the_downloader_per_source() -> None:
    """Each source is asked about once, in the parts' order."""
    recording = RecordingDownloader()
    sources = (
        SourceSpec(name='c', path='z'),
        SourceSpec(name='a', path='x'),
        SourceSpec(name='b', path='y'),
    )
    fresh = refresh_module.head_all(
        parts=BuildParts(
            city_ref=SYDNEY_REF,
            sources=sources,
            downloader=recording,
        ),
    )
    assert recording.heads == ['c', 'a', 'b']
    assert list(fresh) == ['c', 'a', 'b']
    assert fresh['a'] is not None and fresh['a'].etag == 'a'


def test_head_all_gives_none_for_a_failed_source() -> None:
    """A failed HEAD is None and the next source is still asked."""
    recording = RecordingDownloader()
    recording.failing.add('a')
    fresh = refresh_module.head_all(
        parts=BuildParts(
            city_ref=SYDNEY_REF,
            sources=(
                SourceSpec(name='a', path='x'),
                SourceSpec(name='b', path='y'),
            ),
            downloader=recording,
        ),
    )
    assert recording.heads == ['a', 'b']
    assert fresh['a'] is None
    assert fresh['b'] is not None


def test_read_meta_of_missing_file_is_empty(tmp_path: Path) -> None:
    """An unreadable timetable has no meta."""
    assert not refresh_module.read_meta(path=tmp_path / 'missing.db')


class Execed(Exception):
    """Raised by the stand-in for os.execv."""


def test_refresh_hands_over_after_the_load(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With hand_over, the load execs into extras with a checked handoff."""
    seen: dict[str, object] = {}

    def execv(path: str, argv: list[str]) -> None:
        record = handoff_module.read_handoff(path=Path(argv[-1]))
        assert record.tail is not None
        seen.update(
            path=path, argv=argv, record=record,
            inheritable=os.get_inheritable(record.tail.lock_fd),
        )
        raise Execed

    monkeypatch.setattr(os, 'execv', execv)
    with pytest.raises(Execed):
        refresh(
            data_dir=data_dir, city=SYDNEY, parts=parts_for(server=server),
            days=DAYS, error_log=data_dir / 'log' / 'errors.jsonl',
            hand_over=True,
        )
    work = data_dir / 'work'
    assert seen['argv'] == [
        seen['path'], '-m', 'paperroute_transit.build.extras', SYDNEY_REF,
        str(work / 'timetable.new'), str(work / 'handoff.json'),
    ]
    assert seen['inheritable'] is True
    record = seen['record']
    assert isinstance(record, handoff_module.HandoffRecord)
    assert record.tail is not None
    assert record.tail.fetched.changed == ('sydneytrains', 'metro', 'bundle')
    assert record.out == work / 'timetable.db'
    assert not (data_dir / 'timetable.db').exists()
