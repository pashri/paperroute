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
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
    PowerAction,
    PowerRequested,
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
    ('GET', '/settings/power'),
    ('POST', '/settings/power'),
    ('POST', '/power'),
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
    ('both', 'hold', KnobsHeld()),
    ('both', 'press', None),
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


async def test_knob_both_hold_posts_knobs_held(
    display: WebDisplay, client: Client
) -> None:
    """Both-knob hold posts KnobsHeld; any other both action is a 400."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/knob', data={'knob': 'both', 'action': 'hold'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    response = await client.post(
        '/knob', data={'knob': 'both', 'action': 'press'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert posted == [KnobsHeld()]


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
    web_dir = files('paperroute') / 'assets' / 'web'
    html = (web_dir / 'live.html').read_text(encoding='utf-8')
    assert 'data-action="hold"' not in html
    assert html.count('aria-describedby="hint"') == 2
    assert 'id="hint"' in html
    assert html.count('data-action="press"') == 2
    plain = re.sub(r'<[^>]+>', '', ' '.join(html.split()))
    assert 'hold K, or double-click or hold Press, to pin.' in plain
    assert 'hold K or Press, then press J or L' in plain


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
    saved = 'on' if expected else 'off'
    assert response.headers['Location'] == f'/settings?saved={saved}'
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


@pytest.mark.parametrize(('query', 'checked'), [
    ('?saved=on', True),
    ('?saved=off', False),
    ('?saved=maybe', True),
])
async def test_settings_page_prefers_the_saved_value(
    query: str, checked: bool
) -> None:
    """After a form save the switch shows the posted value, not the old."""
    status: dict[str, object] = {'settings': {'auto_rebuild': True}}
    display = WebDisplay(status=lambda: status)
    client = await _client(display=display)
    try:
        text = await (await client.get(f'/settings{query}')).text()
    finally:
        await client.close()
    assert ('aria-describedby="auto-rebuild-hint" checked>' in text) is (
        checked
    )


async def test_power_post_posts_event(
    display: WebDisplay, client: Client
) -> None:
    """A known action posts PowerRequested; others and other sites fail."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    good = await client.post('/power', json={'action': 'reboot'})
    halt = await client.post('/power', json={'action': 'halt'})
    cross = await client.post(
        '/power',
        json={'action': 'poweroff'},
        headers={'Origin': 'http://evil.example'},
    )
    form = await client.post(
        '/power', data={'action': 'poweroff'}, allow_redirects=False,
    )
    assert good.status == HTTPStatus.NO_CONTENT
    assert halt.status == HTTPStatus.BAD_REQUEST
    assert cross.status == HTTPStatus.FORBIDDEN
    assert form.status == HTTPStatus.SEE_OTHER
    assert form.headers['Location'] == '/settings?power=poweroff'
    assert posted == [
        PowerRequested(action=PowerAction.RESTART),
        PowerRequested(action=PowerAction.OFF),
    ]


async def test_settings_page_has_confirm_dialogs(client: Client) -> None:
    """The Device section has both buttons and both confirm dialogs."""
    text = await (await client.get('/settings')).text()
    assert '<h2 id="device">Device</h2>' in text
    assert '<button type="submit">Restart</button>' in text
    assert '<button type="submit">Shut down</button>' in text
    assert (
        '<p id="poweroff-question">Shut down now? The screen will say '
        "when it's safe to unplug.</p>"
    ) in text
    assert '<p id="reboot-question">Restart now?</p>' in text
    assert text.count('<button value="cancel" autofocus>Cancel</button>') == 2
    assert '<button value="confirm">Shut down</button>' in text
    assert '<button value="confirm">Restart</button>' in text
    assert text.count('<dialog ') == 2
    assert '{{' not in text


@pytest.mark.parametrize(('action', 'question', 'button', 'shown'), [
    (
        'poweroff',
        "Shut down now? The screen will say when it's safe to unplug.",
        'Shut down',
        'Shutting down...',
    ),
    ('reboot', 'Restart now?', 'Restart', 'Restarting...'),
])
async def test_power_works_without_script(
    display: WebDisplay,
    client: Client,
    action: str,
    question: str,
    button: str,
    shown: str,
) -> None:
    """Button, confirm page, its form, then the page saying so."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    confirm = await client.post(f'/settings/power?action={action}')
    text = await confirm.text()
    assert f'<p>{question}</p>' in text
    assert f'name="action" value="{action}"' in text
    assert f'<button type="submit">{button}</button>' in text
    assert '<a class="cancel" href="/settings">Cancel</a>' in text
    assert '{{' not in text
    assert not posted
    done = await client.post('/power', data={'action': action})
    assert done.url.path == '/settings'
    after = await done.text()
    assert f'aria-live="polite">{shown}</p>' in after
    assert posted == [PowerRequested(action=PowerAction(action))]


async def test_power_page_rejects_unknown(client: Client) -> None:
    """The confirm page needs a known action."""
    response = await client.get('/settings/power?action=halt')
    assert response.status == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize('path', ['/power', '/settings/power'])
async def test_post_with_null_origin_is_refused(
    display: WebDisplay, client: Client, path: str,
) -> None:
    """A sandboxed page sends Origin: null; it can't reach the power routes."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        path,
        data={'action': 'poweroff'},
        headers={'Origin': 'null'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not posted


def test_page_hint_names_settings_chord() -> None:
    """The hint says how to open Settings from the page."""
    web_dir = files('paperroute') / 'assets' / 'web'
    html = (web_dir / 'live.html').read_text(encoding='utf-8')
    plain = re.sub(r'<[^>]+>', '', ' '.join(html.split()))
    assert (
        'Hold S and K together, or both Press buttons, for Settings.'
        in plain
    )
