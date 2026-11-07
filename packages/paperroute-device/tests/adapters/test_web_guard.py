"""Tests for the local-network request guard."""

import asyncio
from http import HTTPStatus

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_guard import local_only


async def _ok(request: web.Request) -> web.Response:
    del request
    return web.Response(text='ok')


async def _none(request: web.Request) -> web.Response:
    del request
    return web.Response(status=HTTPStatus.NO_CONTENT)


def _status(
    *,
    method: str,
    path: str,
    remote: str = '127.0.0.1',
    headers: dict[str, str] | None = None,
) -> int:
    """Send one request through the guard and return the status."""
    app = web.Application(
        middlewares=[local_only(remote_of=lambda request: remote)],
    )
    app.add_routes([web.get('/', _ok), web.post('/x', _none)])

    async def run() -> int:
        async with TestClient(TestServer(app)) as client:
            response = await client.request(
                method, path, headers=headers,
            )
            return response.status

    return asyncio.run(run())


def test_public_peer_refused() -> None:
    assert (
        _status(method='GET', path='/', remote='8.8.8.8')
        == HTTPStatus.FORBIDDEN
    )


def test_unknown_host_refused() -> None:
    status = _status(
        method='GET', path='/', headers={'Host': 'evil.example'},
    )
    assert status == HTTPStatus.FORBIDDEN


def test_cross_site_post_refused() -> None:
    status = _status(
        method='POST',
        path='/x',
        headers={'Origin': 'http://evil.example'},
    )
    assert status == HTTPStatus.FORBIDDEN


def test_same_origin_post_allowed() -> None:
    status = _status(method='POST', path='/x')
    assert status == HTTPStatus.NO_CONTENT


def test_cross_site_get_allowed() -> None:
    status = _status(
        method='GET',
        path='/',
        headers={'Origin': 'http://evil.example'},
    )
    assert status == HTTPStatus.OK
