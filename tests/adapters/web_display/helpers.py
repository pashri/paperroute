"""Shared helpers for the web display tests."""

import asyncio

import numpy as np
from aiohttp import ClientResponse, web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_display import WebDisplay

Client = TestClient[web.Request, web.Application]


FRAME = np.full((480, 800), 255, dtype=np.uint8)


POWER_ROUTES = [
    ('GET', '/device/power'),
    ('POST', '/device/power'),
    ('POST', '/power'),
]


async def _client(*, display: WebDisplay) -> Client:
    """Start a client on a free local port."""
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


async def _read_event(*, response: ClientResponse) -> tuple[str, str]:
    """Read one server-sent event as (name, data)."""
    async with asyncio.timeout(2):
        name = (await response.content.readline()).decode().strip()
        data = (await response.content.readline()).decode().strip()
        await response.content.readline()
    return name.removeprefix('event: '), data.removeprefix('data: ')


async def page_text(*, display: WebDisplay, path: str) -> str:
    """
    Fetch one page of a display, then close the client.

    Parameters
    ----------
    display : WebDisplay
        The display to serve the page.
    path : str
        The page's path.

    Returns
    -------
    str
        The page's text.
    """
    client = await _client(display=display)
    try:
        return await (await client.get(path)).text()
    finally:
        await client.close()
