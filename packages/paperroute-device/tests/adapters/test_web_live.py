"""Tests for the kit's live view."""

import asyncio
from collections.abc import Mapping
from http import HTTPStatus

import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_frame import NavItem
from paperroute_device.adapters.web_live import KNOB_HINT, LiveView, live_page
from paperroute_device.adapters.web_shell import web_app
from paperroute_hardware.core.knob_events import Knob, KnobInput, KnobPressed

Client = TestClient[web.Request, web.Application]


async def _client(*, live: LiveView) -> Client:
    """Start a client of a bare app around the live view."""
    client = TestClient(TestServer(web_app(live=live, device=None)))
    await client.start_server()
    return client


def test_frame_png_unavailable_until_shown() -> None:
    """The frame is 503 until one is shown, then a PNG."""
    live = LiveView(knobs=lambda event: None)

    async def main() -> tuple[int, int, str]:
        client = await _client(live=live)
        try:
            before = await client.get('/frame.png')
            live.show(frame=np.zeros((480, 800), np.uint8))
            after = await client.get('/frame.png')
        finally:
            await client.close()
        return before.status, after.status, after.content_type

    assert asyncio.run(main()) == (
        HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.OK, 'image/png',
    )


def test_knob_post_reaches_sink() -> None:
    """A knob post is passed to the sink as an event."""
    got: list[KnobInput] = []
    live = LiveView(knobs=got.append)

    async def main() -> int:
        client = await _client(live=live)
        try:
            response = await client.post(
                '/knob', data={'knob': 'top', 'action': 'press'},
            )
        finally:
            await client.close()
        return response.status

    assert asyncio.run(main()) == HTTPStatus.NO_CONTENT
    assert got == [KnobPressed(knob=Knob.TOP)]


def test_status_has_live_fields() -> None:
    """The status JSON holds the app's fields and the live ones."""
    live = LiveView(knobs=lambda event: None, status=lambda: {'mode': 'x'})

    async def main() -> Mapping[str, object]:
        client = await _client(live=live)
        try:
            response = await client.get('/status')
            found: Mapping[str, object] = await response.json()
        finally:
            await client.close()
        return found

    status = asyncio.run(main())
    assert status['mode'] == 'x'
    assert status['version'] == 0
    uptime = status['uptime_s']
    peak = status['max_rss_mb']
    assert isinstance(uptime, float) and uptime >= 0
    assert isinstance(peak, float) and peak > 0


def test_live_page_default_nav_and_hint() -> None:
    """By default the page links the Device page and gives the knob keys."""
    live = LiveView(knobs=lambda event: None)

    async def main() -> str:
        client = await _client(live=live)
        try:
            response = await client.get('/')
            text = await response.text()
        finally:
            await client.close()
        return text

    text = asyncio.run(main())
    assert 'href="/device"' in text
    assert 'href="/pins"' not in text
    assert KNOB_HINT in text


def test_hint_mark_in_nav_stays_text() -> None:
    """A nav label that reads like the hint mark is not replaced."""
    item = NavItem(href='/odd', label='{{hint}}', icon='')
    text = live_page(nav=(item,), hint='HINT')
    assert '<span>{{hint}}</span>' in text
    assert text.count('HINT') == 1
