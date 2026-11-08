"""Tests for fetching and recording the timetable sources."""

import hashlib
import os
import traceback
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Final

import pytest

from paperroute.build.source_state import (
    SourceError,
    SourceState,
    load_states,
    save_states,
)
from paperroute.build.sources import (
    SOURCES,
    SourceSpec,
    download,
    head,
    needs_download,
)
from paperroute_device.adapters.rate_limit import SyncStartLimiter
from tests.build.feed_server import (
    REDIRECT,
    TARGET,
    FeedServer,
    Served,
    feed_server,
)

REAL: Final[Path] = Path(__file__).parent.parent / 'fixtures' / 'real'
KEY: Final[str] = 'secret-key-0123456789'
SPEC: Final[SourceSpec] = SourceSpec(
    name='metro',
    path='v2/gtfs/schedule/metro',
)
SAME: Final[SourceState] = SourceState(
    etag='"1"',
    last_modified='Thu, 15 Oct 2026 10:00:00 GMT',
    sha256='abc',
)


@pytest.fixture(name='server')
def _server() -> Iterator[FeedServer]:
    """
    A running feed server.

    Yields
    ------
    FeedServer
        Server, closed after the test.
    """
    with feed_server() as server:
        yield server


def test_sources_load_rail_feeds_before_bundle() -> None:
    """The preferred feeds come first so their trips win the merge."""
    assert [spec.name for spec in SOURCES] == [
        'sydneytrains',
        'metro',
        'bundle',
    ]


@pytest.mark.parametrize(
    ('stored', 'fresh', 'zip_exists', 'expected'),
    [
        (None, SAME, True, True),
        (SAME, SAME, False, True),
        (SAME, SourceState(etag='"2"', last_modified=SAME.last_modified,
                           sha256=None), True, True),
        (SAME, SourceState(etag=SAME.etag, last_modified='Fri',
                           sha256=None), True, True),
        (SAME, SourceState(etag=None, last_modified=None, sha256=None),
         True, True),
        (SAME, SourceState(etag=SAME.etag, last_modified=SAME.last_modified,
                           sha256=None), True, False),
    ],
    ids=[
        'no-stored-state',
        'missing-zip',
        'etag-change',
        'last-modified-change',
        'neither-header',
        'all-same',
    ],
)
def test_needs_download_matrix(
    stored: SourceState | None,
    fresh: SourceState,
    zip_exists: bool,
    expected: bool,
) -> None:
    """Download only when the headers can't prove the zip is current."""
    assert needs_download(
        stored=stored,
        fresh=fresh,
        zip_exists=zip_exists,
    ) is expected


def test_states_round_trip(tmp_path: Path) -> None:
    """Saved states load back unchanged; a missing file loads as empty."""
    path = tmp_path / 'sources' / 'sources.json'
    assert not load_states(path=path)
    path.parent.mkdir()
    save_states(path=path, states={'metro': SAME})
    assert load_states(path=path) == {'metro': SAME}
    assert [entry.name for entry in path.parent.iterdir()] == [
        'sources.json',
    ]


def test_head_returns_headers_only(server: FeedServer) -> None:
    """HEAD reads the validators and downloads nothing."""
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    state = head(
        base_url=server.base_url,
        spec=SPEC,
        api_key=KEY,
        timeout_s=5,
    )
    assert state == SourceState(
        etag=SAME.etag,
        last_modified=SAME.last_modified,
        sha256=None,
    )
    assert server.state.hits == {'HEAD': 1}
    assert server.state.auth_seen['/' + SPEC.path] == f'apikey {KEY}'


def test_download_streams_and_hashes(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """The returned digest is the SHA-256 of the bytes written."""
    body = (REAL / 'bundle.zip').read_bytes()
    server.state.files['/' + SPEC.path] = Served(body=body)
    out = tmp_path / 'metro.zip'
    digest = download(
        base_url=server.base_url,
        spec=SPEC,
        api_key=KEY,
        out=out,
        timeout_s=5,
    )
    assert digest == hashlib.sha256(body).hexdigest()
    assert out.read_bytes() == body


def test_download_fsyncs_zip(
    server: FeedServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zip is on disk, not just in the page cache, when it returns."""
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', fsync)
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    out = tmp_path / 'metro.zip'
    download(
        base_url=server.base_url,
        spec=SPEC,
        api_key=KEY,
        out=out,
        timeout_s=5,
    )
    assert out.stat().st_ino in synced


def test_download_error_hides_key(server: FeedServer, tmp_path: Path) -> None:
    """A 401 names the source and status, never the key."""
    server.state.status = HTTPStatus.UNAUTHORIZED
    with pytest.raises(SourceError) as caught:
        download(
            base_url=server.base_url,
            spec=SPEC,
            api_key=KEY,
            out=tmp_path / 'metro.zip',
            timeout_s=5,
        )
    assert str(caught.value) == 'metro: HTTP 401'
    assert KEY not in str(caught.value)
    assert KEY not in ''.join(traceback.format_exception(caught.value))


def test_head_error_hides_key(server: FeedServer) -> None:
    """A failed HEAD raises SourceError without the key."""
    server.state.status = HTTPStatus.FORBIDDEN
    with pytest.raises(SourceError, match='metro: HTTP 403') as caught:
        head(base_url=server.base_url, spec=SPEC, api_key=KEY, timeout_s=5)
    assert KEY not in ''.join(traceback.format_exception(caught.value))


def test_download_short_body_raises(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """A body cut short of its Content-Length is an error."""
    server.state.files['/' + SPEC.path] = Served(body=b'x' * 10_000)
    server.state.fail_midway.add('/' + SPEC.path)
    with pytest.raises(SourceError, match='metro: IncompleteRead'):
        download(
            base_url=server.base_url,
            spec=SPEC,
            api_key=KEY,
            out=tmp_path / 'metro.zip',
            timeout_s=5,
        )


def test_download_disk_error_raises(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """A file that can't be written is a SourceError."""
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    with pytest.raises(SourceError, match='metro: FileNotFoundError'):
        download(
            base_url=server.base_url,
            spec=SPEC,
            api_key=KEY,
            out=tmp_path / 'missing' / 'metro.zip',
            timeout_s=5,
        )


def test_download_unreachable_raises(tmp_path: Path) -> None:
    """A refused connection is a SourceError naming the error class."""
    with feed_server() as server:
        base_url = server.base_url
    with pytest.raises(SourceError, match='metro: URLError'):
        download(
            base_url=base_url,
            spec=SPEC,
            api_key=KEY,
            out=tmp_path / 'metro.zip',
            timeout_s=5,
        )


def test_redirect_does_not_forward_key(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """The key goes to the first host only, not to a redirect target."""
    server.state.files[TARGET] = Served(body=b'zip')
    download(
        base_url=server.base_url,
        spec=SourceSpec(name='metro', path=REDIRECT.lstrip('/')),
        api_key=KEY,
        out=tmp_path / 'metro.zip',
        timeout_s=5,
    )
    assert server.state.auth_seen == {
        REDIRECT: f'apikey {KEY}',
        TARGET: None,
    }


def test_requests_wait_for_their_start_slot(
    server: FeedServer, tmp_path: Path,
) -> None:
    """A HEAD and a GET on one limiter start a slot apart."""
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    waits: list[float] = []
    limiter = SyncStartLimiter(
        per_second=1, clock=lambda: 0.0, sleep=waits.append,
    )
    head(
        base_url=server.base_url, spec=SPEC, api_key=KEY, timeout_s=5,
        limiter=limiter,
    )
    download(
        base_url=server.base_url, spec=SPEC, api_key=KEY, timeout_s=5,
        out=tmp_path / 'out.zip', limiter=limiter,
    )
    assert waits == pytest.approx([0.0, 1.01])
