"""Tests for refresh: stored zips."""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from paperroute_transit.build.source_state import SourceError
from paperroute_transit.build.swap import TimetableCheckError
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
    *,
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
    *,
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
