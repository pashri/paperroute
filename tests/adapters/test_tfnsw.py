"""Tests for the TfNSW HTTP feed client, against a real local server."""

import asyncio
from collections.abc import AsyncIterator
from http import HTTPStatus
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from paperroute.adapters.tfnsw import (
    AiohttpFeedsClient,
    FeedError,
    api_key_from_env,
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
        api_key='secret', base_url=str(server.make_url('/'))
    )
    try:
        assert await client.fetch(feed='v1/gtfs/realtime/buses') == b'payload'
    finally:
        await client.close()


async def test_fetch_raises_feed_error_on_429(server: TestServer) -> None:
    """Non-200 answers raise FeedError without the key in the message."""
    client = AiohttpFeedsClient(
        api_key='wrong', base_url=str(server.make_url('/'))
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
            api_key='secret',
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
        api_key='secret',
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
