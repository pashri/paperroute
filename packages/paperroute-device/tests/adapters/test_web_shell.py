"""Tests for the kit's guarded web app."""

import subprocess
import sys
from typing import Final

from aiohttp import web

from paperroute_device.adapters.web_live import LiveView
from paperroute_device.adapters.web_shell import web_app
from paperroute_device_testkit.device_pages import DeviceSite

KIT_ONLY: Final[frozenset[str]] = frozenset(
    {
        'paperroute_device',
        'paperroute_device_testkit',
        'paperroute_hardware',
    },
)
BARE_APP: Final[str] = """
import asyncio
import sys
from http import HTTPStatus

import numpy as np
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_frame import shell_bytes
from paperroute_device.adapters.web_live import LiveView
from paperroute_device.adapters.web_shell import web_app
from paperroute_hardware.core.knob_events import Knob, KnobPressed
from paperroute_device_testkit.device_pages import HOTSPOT, DeviceSite

EVIL = {'Origin': 'http://evil.example'}
PRESS = {'knob': 'top', 'action': 'press'}
SHELL = ('live.css', 'fonts/public-sans.woff2')


async def check(client):
    for path in (
        '/', '/live.js', '/status', '/device', '/setup',
        '/device/power?action=reboot',
    ):
        assert (await client.get(path)).status == HTTPStatus.OK, path
    live.show(frame=np.zeros((480, 800), np.uint8))
    assert (await client.get('/frame.png')).status == HTTPStatus.OK
    for name in SHELL:
        response = await client.get(f'/{name}')
        assert response.status == HTTPStatus.OK, name
        assert await response.read() == shell_bytes(name=name), name
    for path in ('/board.css', '/vendor/tom-select.css', '/settings'):
        assert (await client.get(path)).status == HTTPStatus.NOT_FOUND, path
    pressed = await client.post('/knob', data=PRESS)
    assert pressed.status == HTTPStatus.NO_CONTENT
    for path, form in (('/power', {'action': 'reboot'}), ('/knob', PRESS)):
        response = await client.post(path, data=form, headers=EVIL)
        assert response.status == HTTPStatus.FORBIDDEN, path


async def serve(app, checks):
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        return await checks(client)
    finally:
        await client.close()


async def far(client):
    return (await client.get('/')).status


async def main():
    await serve(web_app(live=live, device=site.pages()), check)
    assert site.posted == [], site.posted
    assert got == [KnobPressed(knob=Knob.TOP)], got
    outside = web_app(
        live=live, device=None, remote_of=lambda request: '8.8.8.8',
    )
    assert await serve(outside, far) == HTTPStatus.FORBIDDEN


got = []
site = DeviceSite(status=HOTSPOT)
live = LiveView(knobs=got.append)
asyncio.run(main())
print(' '.join(sorted({
    name.split('.')[0]
    for name in sys.modules
    if name.startswith('paperroute')
})))
"""


async def _extra(request: web.Request) -> web.Response:
    """Answer an app route."""
    del request
    return web.Response()


def _get(path: str) -> list[str]:
    """The route lines a GET registers."""
    return [f'HEAD {path}', f'GET {path}']


def test_web_app_registers_in_order() -> None:
    """Live and shell routes come first, then the app's, then the device's."""
    app = web_app(
        live=LiveView(knobs=lambda event: None),
        routes=[web.get('/extra', _extra), web.post('/extra', _extra)],
        device=DeviceSite().pages(),
    )
    lines = [
        f'{route.method} {route.resource.canonical}'
        for route in app.router.routes()
        if route.resource is not None
    ]
    assert lines == [
        *_get('/'),
        *_get('/live.css'),
        *_get('/fonts/public-sans.woff2'),
        *_get('/live.js'),
        *_get('/frame.png'),
        *_get('/events'),
        'POST /knob',
        *_get('/status'),
        *_get('/extra'),
        'POST /extra',
        *_get('/device/power'),
        'POST /device/power',
        'POST /power',
        *_get('/setup'),
        *_get('/setup.js'),
        'POST /setup',
        *_get('/device'),
        *_get('/device.js'),
        'POST /device/wifi',
        'POST /device/reset',
        'POST /device/key',
        *_get('/device/errors.jsonl'),
        *_get('/device/state.json'),
    ]


def test_bare_app_serves_kit_without_paperroute() -> None:
    """The kit alone serves the live view and device pages, guarded."""
    done = subprocess.run(
        [sys.executable, '-c', BARE_APP],
        capture_output=True,
        check=False,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert set(done.stdout.split()) <= KIT_ONLY
