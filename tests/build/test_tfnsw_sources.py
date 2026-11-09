"""Tests for Sydney's timetable sources and the TfNSW downloader."""

import hashlib
import os
import traceback
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_sydney.adapters.tfnsw import NoApiKey
from paperroute_sydney.build.__main__ import SYDNEY_BUILD
from paperroute_sydney.build.tfnsw_sources import (
    SYDNEY_SOURCES,
    TfnswDownloader,
)
from paperroute_sydney.city import SYDNEY
from paperroute_transit.build.extras import city_named
from paperroute_transit.build.source_state import SourceError, SourceState
from paperroute_transit.build.sources import SourceSpec
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
VALIDATORS: Final[SourceState] = SourceState(
    etag='"1"',
    last_modified='Thu, 15 Oct 2026 10:00:00 GMT',
    sha256=None,
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


def downloader(*, server: FeedServer) -> TfnswDownloader:
    """
    A downloader with a fake key, pointed at the test server.

    Parameters
    ----------
    server : FeedServer
        Feed server.

    Returns
    -------
    TfnswDownloader
        The downloader.
    """
    return TfnswDownloader(key=lambda: KEY, base_url=server.base_url)


def test_sydney_sources_keep_order() -> None:
    """The preferred rail feeds come first so their trips win the merge."""
    assert [spec.name for spec in SYDNEY_SOURCES] == [
        'sydneytrains',
        'metro',
        'bundle',
    ]
    assert [spec.path for spec in SYDNEY_SOURCES] == [
        'v1/gtfs/schedule/sydneytrains',
        'v2/gtfs/schedule/metro',
        'v1/publictransport/timetables/complete/gtfs',
    ]


def test_sydney_build_names_sydney() -> None:
    """Sydney's build parts name Sydney's city."""
    assert city_named(ref=SYDNEY_BUILD.city_ref) is SYDNEY
    assert SYDNEY_BUILD.sources == SYDNEY_SOURCES


def test_downloader_sends_apikey_header(server: FeedServer) -> None:
    """HEAD reads the validators, downloads nothing and sends the key."""
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    state = downloader(server=server).head(
        spec=SPEC,
        timeout_s=5,
        limiter=None,
    )
    assert state == VALIDATORS
    assert server.state.hits == {'HEAD': 1}
    assert server.state.auth_seen['/' + SPEC.path] == f'apikey {KEY}'


def test_downloader_reads_the_key_per_request(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """A key changed between requests is sent on the next one."""
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    keys = iter(['first-key-0123456789', 'second-key-0123456789'])
    got = TfnswDownloader(key=lambda: next(keys), base_url=server.base_url)
    got.head(spec=SPEC, timeout_s=5, limiter=None)
    assert server.state.auth_seen['/' + SPEC.path] == (
        'apikey first-key-0123456789'
    )
    got.download(
        spec=SPEC,
        out=tmp_path / 'metro.zip',
        timeout_s=5,
        limiter=None,
    )
    assert server.state.auth_seen['/' + SPEC.path] == (
        'apikey second-key-0123456789'
    )


def test_download_streams_and_hashes(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """The returned digest is the SHA-256 of the bytes written."""
    body = (REAL / 'bundle.zip').read_bytes()
    server.state.files['/' + SPEC.path] = Served(body=body)
    out = tmp_path / 'metro.zip'
    digest = downloader(server=server).download(
        spec=SPEC,
        out=out,
        timeout_s=5,
        limiter=None,
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
    downloader(server=server).download(
        spec=SPEC,
        out=out,
        timeout_s=5,
        limiter=None,
    )
    assert out.stat().st_ino in synced


def test_download_error_hides_key(server: FeedServer, tmp_path: Path) -> None:
    """A 401 names the source and status, never the key."""
    server.state.status = HTTPStatus.UNAUTHORIZED
    with pytest.raises(SourceError) as caught:
        downloader(server=server).download(
            spec=SPEC,
            out=tmp_path / 'metro.zip',
            timeout_s=5,
            limiter=None,
        )
    assert str(caught.value) == 'metro: HTTP 401'
    assert KEY not in str(caught.value)
    assert KEY not in ''.join(traceback.format_exception(caught.value))


def test_head_error_hides_key(server: FeedServer) -> None:
    """A failed HEAD raises SourceError without the key."""
    server.state.status = HTTPStatus.FORBIDDEN
    with pytest.raises(SourceError, match='metro: HTTP 403') as caught:
        downloader(server=server).head(spec=SPEC, timeout_s=5, limiter=None)
    assert KEY not in ''.join(traceback.format_exception(caught.value))


def test_missing_key_is_a_source_error(server: FeedServer) -> None:
    """A key file that can't be read fails the source, not the refresh."""

    def no_key() -> str:
        raise NoApiKey('No TfNSW API key in /tmp/secrets')

    got = TfnswDownloader(key=no_key, base_url=server.base_url)
    with pytest.raises(SourceError, match='metro: No TfNSW API key'):
        got.head(spec=SPEC, timeout_s=5, limiter=None)
    assert not server.state.hits


def test_download_short_body_raises(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """A body cut short of its Content-Length is an error."""
    server.state.files['/' + SPEC.path] = Served(body=b'x' * 10_000)
    server.state.fail_midway.add('/' + SPEC.path)
    with pytest.raises(SourceError, match='metro: IncompleteRead'):
        downloader(server=server).download(
            spec=SPEC,
            out=tmp_path / 'metro.zip',
            timeout_s=5,
            limiter=None,
        )


def test_download_disk_error_raises(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """A file that can't be written is a SourceError."""
    server.state.files['/' + SPEC.path] = Served(body=b'zip')
    with pytest.raises(SourceError, match='metro: FileNotFoundError'):
        downloader(server=server).download(
            spec=SPEC,
            out=tmp_path / 'missing' / 'metro.zip',
            timeout_s=5,
            limiter=None,
        )


def test_download_unreachable_raises(tmp_path: Path) -> None:
    """A refused connection is a SourceError naming the error class."""
    with feed_server() as server:
        gone = downloader(server=server)
    with pytest.raises(SourceError, match='metro: URLError'):
        gone.download(
            spec=SPEC,
            out=tmp_path / 'metro.zip',
            timeout_s=5,
            limiter=None,
        )


def test_redirect_does_not_forward_key(
    server: FeedServer,
    tmp_path: Path,
) -> None:
    """The key goes to the first host only, not to a redirect target."""
    server.state.files[TARGET] = Served(body=b'zip')
    downloader(server=server).download(
        spec=SourceSpec(name='metro', path=REDIRECT.lstrip('/')),
        out=tmp_path / 'metro.zip',
        timeout_s=5,
        limiter=None,
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
    got = downloader(server=server)
    got.head(spec=SPEC, timeout_s=5, limiter=limiter)
    got.download(
        spec=SPEC, timeout_s=5, out=tmp_path / 'out.zip', limiter=limiter,
    )
    assert waits == pytest.approx([0.0, 1.01])
