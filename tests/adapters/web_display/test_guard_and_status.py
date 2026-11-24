"""Tests for web display: guard and status."""

import re
import time
from http import HTTPStatus

import pytest

from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_device.adapters.web_live import knob_event, live_page
from paperroute_hardware.core.knob_events import Knob, KnobHeld, KnobHeldTurned
from paperroute_sydney.adapters.web_board import SYDNEY_HINT, SYDNEY_NAV
from paperroute_sydney.city import SYDNEY
from tests.adapters.test_web_device import sydney_site
from tests.adapters.transit_web import transit_display
from tests.adapters.web_display.helpers import (
    FRAME,
    POWER_ROUTES,
    Client,
    _client,
    _read_event,
)

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
    *POWER_ROUTES,
]


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
    display = transit_display(
        remote_of=lambda request: remote,
        device=sydney_site().pages(),
        city=SYDNEY,
    )
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
    display = transit_display(
        remote_of=lambda request: remote,
        device=sydney_site().pages(),
        city=SYDNEY,
    )
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
    display = transit_display(status=lambda: {'frames': 7}, city=SYDNEY)
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
    html = (SHELL_WEB / 'live.html').read_text(encoding='utf-8')
    assert (SHELL_WEB / 'live.css').is_file()
    assert (SHELL_WEB / 'live.js').is_file()
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
    assert boot == display.live.boot
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


async def test_status_carries_line() -> None:
    """The status JSON carries the app's line, and none without one."""
    lined = transit_display(
        status=lambda: {'frames': 7},
        status_line=lambda *, status: f'{status["frames"]} frames',
        city=SYDNEY,
    )
    bodies = []
    for display in (lined, transit_display(status=dict, city=SYDNEY)):
        client = await _client(display=display)
        try:
            bodies.append(await (await client.get('/status')).json())
        finally:
            await client.close()
    assert bodies[0]['line'] == '7 frames'
    assert bodies[0]['frames'] == 7
    assert 'line' not in bodies[1]


async def test_status_built_in_keys_win() -> None:
    """Extras cannot overwrite the built-in status keys."""
    display = transit_display(
        status=lambda: {'version': 99, 'uptime_s': 'x'},
        city=SYDNEY,
    )
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


@pytest.mark.parametrize(('action', 'steps'), [
    ('hold-left', -1),
    ('hold-right', 1),
])
def test_knob_event_held_turns(action: str, steps: int) -> None:
    """The hold-left and hold-right actions are turns while held."""
    assert knob_event(knob='bottom', action=action) == KnobHeldTurned(
        knob=Knob.BOTTOM, steps=steps,
    )


def test_page_has_no_hold_buttons() -> None:
    """The page has two Press buttons, no Hold buttons, and the new hint."""
    html = live_page(nav=SYDNEY_NAV, hint=SYDNEY_HINT)
    assert 'data-action="hold"' not in html
    assert html.count('aria-describedby="hint"') == 2
    assert 'id="hint"' in html
    assert html.count('data-action="press"') == 2
    plain = re.sub(r'<[^>]+>', '', ' '.join(html.split()))
    assert 'hold K, or double-click or hold Press, to pin.' in plain
    assert 'hold K or Press, then press J or L' in plain


def test_page_click_handler_constants() -> None:
    """The script has a click listener, a zero-detail check and the timings."""
    script = (SHELL_WEB / 'live.js').read_text(encoding='utf-8')
    assert "addEventListener('click'" in script
    assert 'event.detail === 0' in script
    assert 'HOLD_MS = 800' in script
    assert 'DOUBLE_MS = 300' in script
