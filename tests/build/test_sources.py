"""Tests for deciding on and recording timetable downloads."""

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from paperroute_transit.build.source_state import (
    SourceError,
    SourceState,
    load_states,
    save_states,
)
from paperroute_transit.build.sources import (
    SourceSpec,
    UrlDownloader,
    needs_download,
)
from tests.build.feed_server import FeedServer, Served, feed_server

SAME: Final[SourceState] = SourceState(
    etag='"1"',
    last_modified='Thu, 15 Oct 2026 10:00:00 GMT',
    sha256='abc',
)
PLAIN: Final[SourceSpec] = SourceSpec(name='plain', path='plain/')
BIG: Final[SourceSpec] = SourceSpec(
    name='big', path='big/', size_limit=1000,
)


@pytest.fixture(name='server')
def _server() -> Iterator[FeedServer]:
    """
    A running feed server holding one zip with no validators.

    Yields
    ------
    FeedServer
        Server, closed after the test.
    """
    with feed_server() as server:
        server.state.files['/plain/'] = Served(
            body=b'zip bytes', etag=None, last_modified=None,
        )
        yield server


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


def test_url_downloader_sends_no_authorization(server: FeedServer) -> None:
    """Url downloader sends no authorization."""
    UrlDownloader(base_url=server.base_url).head(
        spec=PLAIN, timeout_s=5, limiter=None,
    )
    assert 'authorization' not in {
        name.lower() for name in server.last_headers()
    }


def test_url_downloader_reads_no_validators_when_none_are_sent(
    server: FeedServer,
) -> None:
    """Url downloader reads no validators when none are sent."""
    state = UrlDownloader(base_url=server.base_url).head(
        spec=PLAIN, timeout_s=5, limiter=None,
    )
    assert state == SourceState(etag=None, last_modified=None, sha256=None)


def test_url_downloader_writes_the_zip_and_returns_its_sha256(
    server: FeedServer, tmp_path: Path,
) -> None:
    """Url downloader writes the zip and returns its sha256."""
    digest = UrlDownloader(base_url=server.base_url).download(
        spec=PLAIN, out=tmp_path / 'p.zip', timeout_s=5, limiter=None,
    )
    assert digest == hashlib.sha256(
        (tmp_path / 'p.zip').read_bytes(),
    ).hexdigest()


def test_url_downloader_sends_the_headers_it_is_given(
    server: FeedServer,
) -> None:
    """Url downloader sends the headers it is given."""
    downloader = UrlDownloader(
        base_url=server.base_url,
        headers=lambda *, spec: {'X-Test': spec.name},
    )
    downloader.head(spec=PLAIN, timeout_s=5, limiter=None)
    assert server.last_headers()['X-Test'] == 'plain'


@pytest.mark.parametrize('length', [True, False], ids=['length', 'none'])
def test_url_downloader_abandons_a_body_over_its_limit(
    server: FeedServer, tmp_path: Path, length: bool,
) -> None:
    """A body past the limit fails and leaves no file."""
    server.state.files['/big/'] = Served(body=b'x' * 2000, length=length)
    out = tmp_path / 'big.zip'
    with pytest.raises(SourceError, match='big: DownloadTooLarge'):
        UrlDownloader(base_url=server.base_url).download(
            spec=BIG, out=out, timeout_s=5, limiter=None,
        )
    assert not out.exists()


def test_url_downloader_keeps_a_body_at_its_limit(
    server: FeedServer, tmp_path: Path,
) -> None:
    """A body of exactly the limit is downloaded."""
    server.state.files['/big/'] = Served(body=b'x' * 1000, length=False)
    out = tmp_path / 'big.zip'
    UrlDownloader(base_url=server.base_url).download(
        spec=BIG, out=out, timeout_s=5, limiter=None,
    )
    assert out.stat().st_size == 1000


def test_url_downloader_removes_a_partial_file(
    server: FeedServer, tmp_path: Path,
) -> None:
    """A body cut short leaves nothing behind."""
    server.state.files['/plain/'] = Served(body=b'x' * 10_000)
    server.state.fail_midway.add('/plain/')
    out = tmp_path / 'p.zip'
    with pytest.raises(SourceError, match='plain: IncompleteRead'):
        UrlDownloader(base_url=server.base_url).download(
            spec=PLAIN, out=out, timeout_s=5, limiter=None,
        )
    assert not out.exists()
