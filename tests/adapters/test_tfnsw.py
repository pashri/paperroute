"""Tests for the TfNSW HTTP feed client, against a real local server."""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Final

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from paperroute.adapters.rate_limit import StartLimiter
from paperroute.adapters.tfnsw import (
    AiohttpFeedsClient,
    FeedError,
    FeedThrottled,
    NoApiKey,
    api_key_from_env,
    read_retry_after,
)


async def feed_handler(request: web.Request) -> web.Response:
    """
    Serve bytes, or 429 when the key is wrong.

    Parameters
    ----------
    request : web.Request
        Incoming request.

    Returns
    -------
    web.Response
        Response.
    """
    if request.headers.get('Authorization') != 'apikey secret':
        return web.Response(status=HTTPStatus.TOO_MANY_REQUESTS)
    return web.Response(body=b'payload')


@pytest.fixture(name='server')
async def _server() -> AsyncIterator[TestServer]:
    """
    Run a local feed server.

    Yields
    ------
    TestServer
        Running server.
    """
    app = web.Application()
    app.router.add_get('/v1/gtfs/realtime/buses', feed_handler)
    server = TestServer(app)
    await server.start_server()
    yield server
    await server.close()


async def test_fetch_sends_key_and_returns_body(server: TestServer) -> None:
    """The client sends the apikey header and returns the body."""
    client = AiohttpFeedsClient(
        key=lambda: 'secret', base_url=str(server.make_url('/'))
    )
    try:
        assert await client.fetch(feed='v1/gtfs/realtime/buses') == b'payload'
    finally:
        await client.close()


async def test_fetch_raises_feed_error_on_429(server: TestServer) -> None:
    """Non-200 answers raise FeedError without the key in the message."""
    client = AiohttpFeedsClient(
        key=lambda: 'wrong', base_url=str(server.make_url('/'))
    )
    try:
        with pytest.raises(FeedError) as caught:
            await client.fetch(feed='v1/gtfs/realtime/buses')
        assert '429' in str(caught.value)
        assert 'wrong' not in str(caught.value)
    finally:
        await client.close()


async def test_close_leaves_injected_session_open(server: TestServer) -> None:
    """A session passed in stays open; the caller closes it."""
    async with aiohttp.ClientSession() as session:
        client = AiohttpFeedsClient(
            key=lambda: 'secret',
            session=session,
            base_url=str(server.make_url('/')),
        )
        await client.fetch(feed='v1/gtfs/realtime/buses')
        await client.close()
        assert not session.closed


async def slow_handler(request: web.Request) -> web.Response:
    """
    Answer after the client's timeout has passed.

    Parameters
    ----------
    request : web.Request
        Incoming request.

    Returns
    -------
    web.Response
        Response, which the client never sees.
    """
    await asyncio.sleep(1)
    return web.Response(body=request.path.encode())


async def test_fetch_raises_feed_error_on_timeout() -> None:
    """A slow server gives FeedError naming TimeoutError, not the key."""
    app = web.Application()
    app.router.add_get('/v1/gtfs/realtime/buses', slow_handler)
    server = TestServer(app)
    await server.start_server()
    client = AiohttpFeedsClient(
        key=lambda: 'secret',
        base_url=str(server.make_url('/')),
        timeout_s=0.05,
    )
    try:
        with pytest.raises(FeedError) as caught:
            await client.fetch(feed='v1/gtfs/realtime/buses')
        assert 'TimeoutError' in str(caught.value)
        assert 'secret' not in str(caught.value)
    finally:
        await client.close()
        await server.close()


def test_api_key_from_env_reads_named_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key is the named file's text without surrounding whitespace."""
    key_file = tmp_path / 'secrets'
    key_file.write_text('test-key\n', encoding='utf-8')
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(key_file))
    assert api_key_from_env() == 'test-key'


def test_api_key_from_env_exits_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no key file named, the process exits with a message."""
    monkeypatch.delenv('PAPERROUTE_API_KEY_FILE', raising=False)
    with pytest.raises(SystemExit, match='PAPERROUTE_API_KEY_FILE'):
        api_key_from_env()


JITTER_S: Final[float] = 0.1


async def test_burst_is_spaced_and_complete() -> None:
    """A burst all returns, started in call order, at most three a second."""
    starts: list[tuple[float, int]] = []

    async def handler(request: web.Request) -> web.Response:
        starts.append((time.monotonic(), int(request.query['n'])))
        return web.Response(body=b'payload')

    app = web.Application()
    for name in ('buses', 'ferries', 'trains'):
        app.router.add_get(f'/v1/gtfs/realtime/{name}', handler)
    server = TestServer(app)
    await server.start_server()
    client = AiohttpFeedsClient(
        key=lambda: 'secret',
        base_url=str(server.make_url('/')),
        limiter=StartLimiter(),
    )
    names = ('buses', 'ferries', 'trains')
    try:
        bodies = await asyncio.gather(
            *(
                client.fetch(feed=f'v1/gtfs/realtime/{names[n % 3]}?n={n}')
                for n in range(12)
            )
        )
    finally:
        await client.close()
        await server.close()
    assert bodies == [b'payload'] * 12
    times = [moment for moment, _ in starts]
    assert [n for _, n in starts] == list(range(12))
    for first, fourth in zip(times, times[3:]):
        assert fourth - first >= 1 - JITTER_S


async def test_failure_does_not_stall_queue() -> None:
    """A 429 in the burst raises, and later calls still return."""

    async def handler(request: web.Request) -> web.Response:
        if request.query['n'] == '2':
            return web.Response(status=HTTPStatus.TOO_MANY_REQUESTS)
        return web.Response(body=b'payload')

    app = web.Application()
    app.router.add_get('/v1/gtfs/realtime/buses', handler)
    server = TestServer(app)
    await server.start_server()
    client = AiohttpFeedsClient(
        key=lambda: 'secret',
        base_url=str(server.make_url('/')),
        limiter=StartLimiter(per_second=100),
    )
    try:
        results = await asyncio.gather(
            *(
                client.fetch(feed=f'v1/gtfs/realtime/buses?n={n}')
                for n in range(5)
            ),
            return_exceptions=True,
        )
    finally:
        await client.close()
        await server.close()
    assert isinstance(results[2], FeedError)
    assert '429' in str(results[2])
    assert [r for i, r in enumerate(results) if i != 2] == [b'payload'] * 4


async def test_fetch_without_key_sends_nothing() -> None:
    """With no key no request is made, and the error says so."""
    seen: list[str | None] = []
    server = await recording_server(seen=seen)
    client = AiohttpFeedsClient(
        key=lambda: None,
        base_url=str(server.make_url('/')),
    )
    try:
        with pytest.raises(FeedError, match='no API key'):
            await client.fetch(feed='v1/gtfs/realtime/buses')
        assert not seen
    finally:
        await client.close()
        await server.close()


async def test_fetch_uses_the_current_key() -> None:
    """Each fetch sends the key the callable gives at that moment."""
    seen: list[str | None] = []
    keys = ['fake-key-one']
    server = await recording_server(seen=seen)
    client = AiohttpFeedsClient(
        key=lambda: keys[0],
        base_url=str(server.make_url('/')),
    )
    try:
        await client.fetch(feed='v1/gtfs/realtime/buses')
        keys[0] = 'fake-key-two'
        await client.fetch(feed='v1/gtfs/realtime/buses')
        assert seen == ['apikey fake-key-one', 'apikey fake-key-two']
    finally:
        await client.close()
        await server.close()


async def recording_server(*, seen: list[str | None]) -> TestServer:
    """
    Start a feed server that records each Authorization header.

    Parameters
    ----------
    seen : list[str | None]
        Gets one header per request.

    Returns
    -------
    TestServer
        Running server; the caller closes it.
    """

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.headers.get('Authorization'))
        return web.Response(body=b'payload')

    app = web.Application()
    app.router.add_get('/v1/gtfs/realtime/buses', handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.parametrize('contents', [None, b'', b'  \n', b'\xff\xfe'])
def test_api_key_from_env_refuses_no_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: bytes | None,
) -> None:
    """A missing, blank or unreadable key file names the file, not a key."""
    key_file = tmp_path / 'secrets'
    if contents is not None:
        key_file.write_bytes(contents)
    monkeypatch.setenv('PAPERROUTE_API_KEY_FILE', str(key_file))
    with pytest.raises(NoApiKey) as caught:
        api_key_from_env()
    assert str(caught.value) == f'No TfNSW API key in {key_file}'


async def test_429_carries_retry_after() -> None:
    """A 429 raises FeedThrottled with the Retry-After seconds."""

    async def handler(_: web.Request) -> web.Response:
        return web.Response(
            status=HTTPStatus.TOO_MANY_REQUESTS,
            headers={'Retry-After': '120'},
        )

    app = web.Application()
    app.router.add_get('/v1/gtfs/realtime/buses', handler)
    server = TestServer(app)
    await server.start_server()
    client = AiohttpFeedsClient(
        key=lambda: 'secret', base_url=str(server.make_url('/')),
    )
    try:
        with pytest.raises(FeedThrottled) as caught:
            await client.fetch(feed='v1/gtfs/realtime/buses')
    finally:
        await client.close()
        await server.close()
    assert str(caught.value) == 'v1/gtfs/realtime/buses: HTTP 429'
    assert caught.value.retry_after_s == 120


async def test_429_without_retry_after(server: TestServer) -> None:
    """A 429 with no Retry-After leaves the wait to the caller."""
    client = AiohttpFeedsClient(
        key=lambda: 'wrong', base_url=str(server.make_url('/'))
    )
    try:
        with pytest.raises(FeedThrottled) as caught:
            await client.fetch(feed='v1/gtfs/realtime/buses')
    finally:
        await client.close()
    assert caught.value.retry_after_s is None


@pytest.mark.parametrize(
    ('value', 'seconds'),
    [
        (None, None),
        ('30', 30.0),
        (' 7 ', 7.0),
        ('Thu, 29 Oct 2026 04:01:00 GMT', 60.0),
        ('Thu, 29 Oct 2026 03:59:00 GMT', 0.0),
        ('soon', None),
        ('', None),
    ],
)
def test_read_retry_after(value: str | None, seconds: float | None) -> None:
    """Seconds and HTTP dates are read; anything else is None."""
    now = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)
    assert read_retry_after(value=value, now=now) == seconds
