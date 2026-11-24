"""Tests for refresh: downloads."""

import fcntl
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest

from paperroute_sydney.build.tfnsw_sources import SYDNEY_SOURCES
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build.refresh import RefreshBusy
from tests.build.feed_server import FeedServer, Served
from tests.build.refresh.helpers import (
    DAYS,
    METRO,
    START,
    metro_zip,
    run,
    snapshot,
    window_start,
)


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
    assert not report.downloaded


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
    assert not report.changed
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
    assert not report.changed


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
