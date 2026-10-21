"""Tests for the web display."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from http import HTTPStatus
from importlib.resources import files

import numpy as np
import pytest
from aiohttp import ClientResponse, web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters import web_display
from paperroute.adapters.web_display import WebDisplay, knob_event
from paperroute.core.messages import (
    Event,
    Knob,
    KnobHeld,
    KnobPressed,
    KnobTurned,
    SettingsChanged,
)

Client = TestClient[web.Request, web.Application]
FRAME = np.full((480, 800), 255, dtype=np.uint8)
ROUTES = [
    ('GET', '/'),
    ('GET', '/live.css'),
    ('GET', '/live.js'),
    ('GET', '/frame.png'),
    ('GET', '/events'),
    ('GET', '/status'),
    ('POST', '/knob'),
    ('GET', '/settings'),
    ('GET', '/settings.js'),
    ('POST', '/settings'),
]


async def _client(*, display: WebDisplay) -> Client:
    """Start a client on a free local port."""
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


@pytest.fixture(name='display')
def _display() -> WebDisplay:
    """A display that has shown nothing yet."""
    return WebDisplay()


@pytest.fixture(name='client')
async def _web_client(display: WebDisplay) -> AsyncIterator[Client]:
    """A client of the display's app."""
    client = await _client(display=display)
    yield client
    await client.close()


async def _read_event(*, response: ClientResponse) -> tuple[str, str]:
    """Read one server-sent event as (name, data)."""
    async with asyncio.timeout(2):
        name = (await response.content.readline()).decode().strip()
        data = (await response.content.readline()).decode().strip()
        await response.content.readline()
    return name.removeprefix('event: '), data.removeprefix('data: ')


@pytest.mark.parametrize(('knob', 'action', 'expected'), [
    ('top', 'left', KnobTurned(knob=Knob.TOP, steps=-1)),
    ('top', 'right', KnobTurned(knob=Knob.TOP, steps=1)),
    ('top', 'press', KnobPressed(knob=Knob.TOP)),
    ('bottom', 'left', KnobTurned(knob=Knob.BOTTOM, steps=-1)),
    ('bottom', 'right', KnobTurned(knob=Knob.BOTTOM, steps=1)),
    ('bottom', 'press', KnobPressed(knob=Knob.BOTTOM)),
    ('bottom', 'hold', KnobHeld(knob=Knob.BOTTOM)),
    ('middle', 'left', None),
    ('top', 'spin', None),
])
def test_knob_event_mapping(
    knob: str, action: str, expected: Event | None
) -> None:
    """Valid knob and action pairs map to events, others to None."""
    assert knob_event(knob=knob, action=action) == expected


async def test_frame_png_503_before_first_frame(
    display: WebDisplay, client: Client
) -> None:
    """There is no frame until the first show."""
    response = await client.get('/frame.png')
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    display.show(frame=FRAME)
    response = await client.get('/frame.png')
    assert response.status == HTTPStatus.OK
    assert response.content_type == 'image/png'
    assert response.headers['Cache-Control'] == 'no-store'
    assert (await response.read()).startswith(b'\x89PNG')


async def test_events_sends_current_frame_version_on_connect(
    display: WebDisplay, client: Client
) -> None:
    """A new stream starts with the version already shown."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    assert response.content_type == 'text/event-stream'
    assert response.headers['Cache-Control'] == 'no-store'
    assert await _read_event(response=response) == ('frame', f'{display.boot}-1')


async def test_events_pushes_new_versions(
    display: WebDisplay, client: Client
) -> None:
    """Each new frame is pushed to an open stream."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    assert await _read_event(response=response) == ('frame', f'{display.boot}-1')
    display.show(frame=FRAME)
    assert await _read_event(response=response) == ('frame', f'{display.boot}-2')
    display.show(frame=FRAME)
    assert await _read_event(response=response) == ('frame', f'{display.boot}-3')


async def test_events_pings_when_idle(
    display: WebDisplay,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quiet stream gets a comment line to keep it open."""
    monkeypatch.setattr(web_display, 'PING_S', 0.05)
    response = await client.get('/events')
    async with asyncio.timeout(2):
        line = await response.content.readline()
    assert line.decode().strip() == ': ping'
    assert display.version == 0


async def test_knob_post_posts_event(
    display: WebDisplay, client: Client
) -> None:
    """A knob POST becomes an event on the bound queue."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/knob',
        data={'knob': 'bottom', 'action': 'right'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    response = await client.post(
        '/knob',
        json={'knob': 'top', 'action': 'press'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    assert posted == [
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        KnobPressed(knob=Knob.TOP),
    ]


@pytest.mark.parametrize('payload', [
    {'knob': 'middle', 'action': 'left'},
    {'knob': 'top'},
])
async def test_knob_post_rejects_unknown(
    client: Client, payload: dict[str, str]
) -> None:
    """Unknown values are a 400."""
    response = await client.post('/knob', data=payload)
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_knob_post_rejects_bad_json(client: Client) -> None:
    """A JSON body that is not an object is a 400."""
    response = await client.post('/knob', json=['top', 'left'])
    assert response.status == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize(('method', 'path'), ROUTES)
@pytest.mark.parametrize('remote', [
    '8.8.8.8',
    None,
    'not-an-ip',
    '::ffff:8.8.8.8',
    '2001:4860::8888',
])
async def test_public_address_is_refused_on_every_route(
    method: str, path: str, remote: str | None
) -> None:
    """Only private and loopback peers are served."""
    display = WebDisplay(remote_of=lambda request: remote)
    client = await _client(display=display)
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.FORBIDDEN
    finally:
        await client.close()


@pytest.mark.parametrize('remote', [
    '192.168.0.19',
    '127.0.0.1',
    '::1',
    'fe80::1',
    'fd00::1',
])
async def test_private_address_is_allowed(remote: str) -> None:
    """Private and loopback peers are served."""
    display = WebDisplay(remote_of=lambda request: remote)
    client = await _client(display=display)
    try:
        response = await client.get('/status')
        assert response.status == HTTPStatus.OK
    finally:
        await client.close()


async def test_knob_cross_site_origin_is_refused(
    client: Client,
) -> None:
    """A POST from another site is refused; the same site is fine."""
    data = {'knob': 'top', 'action': 'press'}
    host = f'{client.host}:{client.port}'
    bad = await client.post(
        '/knob',
        data=data,
        headers={'Origin': 'http://evil.example'},
    )
    good = await client.post(
        '/knob',
        data=data,
        headers={'Origin': f'http://{host}'},
    )
    none = await client.post('/knob', data=data)
    assert bad.status == HTTPStatus.FORBIDDEN
    assert good.status == HTTPStatus.NO_CONTENT
    assert none.status == HTTPStatus.NO_CONTENT


async def test_status_reports_version_and_rss() -> None:
    """The status JSON has the version, memory and the status dict."""
    display = WebDisplay(status=lambda: {'frames': 7})
    client = await _client(display=display)
    try:
        display.show(frame=FRAME)
        body = await (await client.get('/status')).json()
    finally:
        await client.close()
    assert body['version'] == 1
    assert body['frames'] == 7
    assert body['max_rss_mb'] > 0
    assert body['uptime_s'] >= 0


async def test_page_is_served(client: Client) -> None:
    """The page and its assets are served."""
    page = await client.get('/')
    assert page.status == HTTPStatus.OK
    assert page.content_type == 'text/html'
    css = await client.get('/live.css')
    assert css.content_type == 'text/css'
    script = await client.get('/live.js')
    assert script.content_type in {'text/javascript', 'application/javascript'}


def test_page_assets_are_packaged() -> None:
    """The three web files ship with the package."""
    web_dir = files('paperroute') / 'assets' / 'web'
    html = (web_dir / 'live.html').read_text(encoding='utf-8')
    assert (web_dir / 'live.css').is_file()
    assert (web_dir / 'live.js').is_file()
    assert 'live.css' in html
    assert 'live.js' in html
    assert 'role="status"' not in html


async def test_event_data_carries_a_boot_id(
    display: WebDisplay, client: Client
) -> None:
    """The boot id is hex, so a restart changes the image URL."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    _, data = await _read_event(response=response)
    boot, version = data.split('-')
    assert int(boot, 16) >= 0
    assert boot == display.boot
    assert version == '1'


@pytest.mark.parametrize('host', [
    'paperroute.local',
    'paperroute.local:80',
    'localhost',
    '192.168.0.94',
    '192.168.0.94:8080',
    '[fe80::1]',
    '[fe80::1]:8080',
])
async def test_known_host_header_is_allowed(
    client: Client, host: str
) -> None:
    """Names the device answers to are served."""
    response = await client.get('/status', headers={'Host': host})
    assert response.status == HTTPStatus.OK


@pytest.mark.parametrize('host', ['evil.example', 'evil.example:80'])
async def test_unknown_host_header_is_refused(
    client: Client, host: str
) -> None:
    """A rebound DNS name from a private peer is refused."""
    response = await client.get('/status', headers={'Host': host})
    assert response.status == HTTPStatus.FORBIDDEN


async def test_knob_post_rejects_invalid_utf8_json(
    client: Client,
) -> None:
    """A JSON body that is not valid UTF-8 is a 400, not a 500."""
    response = await client.post(
        '/knob',
        data=b'{"knob": "\xff"}',
        headers={'Content-Type': 'application/json'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_status_built_in_keys_win() -> None:
    """Extras cannot overwrite the built-in status keys."""
    display = WebDisplay(status=lambda: {'version': 99, 'uptime_s': 'x'})
    client = await _client(display=display)
    try:
        body = await (await client.get('/status')).json()
    finally:
        await client.close()
    assert body['version'] == 0
    assert isinstance(body['uptime_s'], float)


async def test_shutdown_closes_open_event_streams(
    display: WebDisplay,
) -> None:
    """An open stream does not hold up server shutdown."""
    client = await _client(display=display)
    response = await client.get('/events')
    display.show(frame=FRAME)
    await _read_event(response=response)
    started = time.monotonic()
    await client.server.close()
    elapsed = time.monotonic() - started
    await client.close()
    assert elapsed < 1


@pytest.mark.parametrize('host', ['[', '[::1', 'a]b'])
async def test_malformed_host_header_is_refused(
    client: Client, host: str
) -> None:
    """A Host header that cannot be parsed is a 403, not a 500."""
    response = await client.get('/status', headers={'Host': host})
    assert response.status == HTTPStatus.FORBIDDEN


def test_hold_action_posts_knob_held() -> None:
    """The hold action on the bottom knob becomes a KnobHeld event."""
    event = knob_event(knob='bottom', action='hold')
    assert event == KnobHeld(knob=Knob.BOTTOM)


def test_page_has_no_hold_buttons() -> None:
    """The page has two Press buttons, no Hold buttons, and the new hint."""
    web_dir = files('paperroute') / 'assets' / 'web'
    html = (web_dir / 'live.html').read_text(encoding='utf-8')
    assert 'data-action="hold"' not in html
    assert html.count('aria-describedby="hint"') == 2
    assert 'id="hint"' in html
    assert html.count('data-action="press"') == 2
    plain = re.sub(r'<[^>]+>', '', ' '.join(html.split()))
    assert 'hold K, or double-click or hold Press, to pin.' in plain


def test_page_click_handler_constants() -> None:
    """The script has a click listener, a zero-detail check and the timings."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = (web_dir / 'live.js').read_text(encoding='utf-8')
    assert "addEventListener('click'" in script
    assert 'event.detail === 0' in script
    assert 'HOLD_MS = 800' in script
    assert 'DOUBLE_MS = 300' in script


async def test_settings_post_posts_event(
    display: WebDisplay, client: Client
) -> None:
    """A switch change posts an event; bad values and other sites fail."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    good = await client.post('/settings', json={'auto_rebuild': 'off'})
    maybe = await client.post('/settings', json={'auto_rebuild': 'maybe'})
    cross = await client.post(
        '/settings',
        json={'auto_rebuild': 'on'},
        headers={'Origin': 'http://evil.example'},
    )
    assert good.status == HTTPStatus.NO_CONTENT
    assert maybe.status == HTTPStatus.BAD_REQUEST
    assert cross.status == HTTPStatus.FORBIDDEN
    assert posted == [SettingsChanged(auto_rebuild=False)]


@pytest.mark.parametrize(('fields', 'expected'), [
    ([('auto_rebuild', 'off')], False),
    ([('auto_rebuild', 'off'), ('auto_rebuild', 'on')], True),
])
async def test_settings_form_redirects_back(
    display: WebDisplay,
    client: Client,
    fields: list[tuple[str, str]],
    expected: bool,
) -> None:
    """A plain form post is answered with 303 back to the page."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings', data=fields, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert posted == [SettingsChanged(auto_rebuild=expected)]


@pytest.mark.parametrize(('status', 'checked'), [
    ({}, True),
    ({'settings': {'auto_rebuild': True}}, True),
    ({'settings': {'auto_rebuild': False}}, False),
])
async def test_settings_page_served(
    status: dict[str, object], checked: bool
) -> None:
    """The page has the switch, drawn as reported, and a link to Live."""
    display = WebDisplay(status=lambda: status)
    client = await _client(display=display)
    try:
        page = await client.get('/settings')
        text = await page.text()
        script = await client.get('/settings.js')
    finally:
        await client.close()
    assert page.content_type == 'text/html'
    assert 'Rebuild timetable automatically' in text
    assert (
        'Rebuilds an outdated timetable on the device when a new version'
        in text
    )
    assert '<a href="/">Live</a>' in text
    assert ('aria-describedby="auto-rebuild-hint" checked>' in text) is (
        checked
    )
    assert '{{' not in text
    assert script.content_type in {
        'text/javascript', 'application/javascript',
    }


def test_live_page_links_to_settings() -> None:
    """The Live page's header links to Settings."""
    web_dir = files('paperroute') / 'assets' / 'web'
    html = (web_dir / 'live.html').read_text(encoding='utf-8')
    assert '<a href="/settings">Settings</a>' in html
