"""Tests for starting and stopping the web server."""

import logging
import socket
from http import HTTPStatus

import aiohttp
import pytest
from aiohttp import web

from paperroute_device.adapters.web_server import BindError, serving

HOST = '127.0.0.1'


async def hello(request: web.Request) -> web.Response:
    """
    Answer every request with a greeting.

    Parameters
    ----------
    request : web.Request
        Ignored.

    Returns
    -------
    web.Response
        The greeting.
    """
    return web.Response(text='hello')


def greeting_app() -> web.Application:
    """
    Make an app that greets at `/`.

    Returns
    -------
    web.Application
        The app.
    """
    app = web.Application()
    app.add_routes([web.get('/', hello)])
    return app


def free_port() -> int:
    """
    Find a port nothing is listening on.

    Returns
    -------
    int
        Port number.
    """
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


async def test_listen_reports_busy_port(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A port another socket listens on raises BindError and is logged."""
    with socket.socket() as held:
        held.bind((HOST, 0))
        held.listen()
        port = int(held.getsockname()[1])
        with caplog.at_level(logging.ERROR), pytest.raises(BindError):
            async with serving(app=greeting_app(), host=HOST, port=port):
                pass
    assert f'cannot serve on {HOST}:{port}' in caplog.text


async def test_serving_stops_on_exit() -> None:
    """The app answers inside the block and nothing listens after it."""
    port = free_port()
    url = f'http://{HOST}:{port}/'
    async with serving(app=greeting_app(), host=HOST, port=port):
        async with (
            aiohttp.ClientSession() as session,
            session.get(url) as response,
        ):
            status, text = response.status, await response.text()
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientConnectorError):
            async with session.get(url):
                pass
    assert (status, text) == (HTTPStatus.OK, 'hello')
