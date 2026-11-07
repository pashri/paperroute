"""Tests for the web display."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import time as clock
from html.parser import HTMLParser
from http import HTTPStatus
from importlib.resources import files

import numpy as np
import pytest
from aiohttp import ClientResponse, web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters import web_display
from paperroute.adapters.settings_form import ERRORS
from paperroute.adapters.web_display import WebDisplay, knob_event
from paperroute.core.messages import (
    Event,
    PowerAction,
    PowerRequested,
    SettingsChanged,
)
from paperroute.core.saved import LiveView, Lookahead, Rate, Window
from paperroute.core.state import step
from paperroute.render.canvas import encode_png
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from tests.adapters.test_web_device import Device
from tests.core.test_quiet import asleep_at_one
from tests.page_markup import parse_page

Client = TestClient[web.Request, web.Application]
FRAME = np.full((480, 800), 255, dtype=np.uint8)
POWER_ROUTES = [
    ('GET', '/device/power'),
    ('POST', '/device/power'),
    ('POST', '/power'),
]
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


async def _client(*, display: WebDisplay) -> Client:
    """Start a client on a free local port."""
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


@pytest.fixture(name='display')
def _display() -> WebDisplay:
    """A display with device pages that has shown nothing yet."""
    return WebDisplay(device=Device().pages())


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


async def test_web_copy_stays_upright(
    display: WebDisplay, client: Client
) -> None:
    """A turned frame is served as drawn, the right way up."""
    frame = FRAME.copy()
    frame[0, 0] = 0
    display.show(frame=frame, full=True, turned=True)
    response = await client.get('/frame.png')
    assert await response.read() == encode_png(grey=frame)


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
    display = WebDisplay(
        remote_of=lambda request: remote,
        device=Device().pages(),
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
    display = WebDisplay(
        remote_of=lambda request: remote,
        device=Device().pages(),
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


@pytest.mark.parametrize(('status', 'checked'), [
    ({}, True),
    ({'settings': {'auto_rebuild': 'on'}}, True),
    ({'settings': {'auto_rebuild': 'off'}}, False),
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
    assert ('/', False, 'Live') in parse_page(text=text).links
    assert ('aria-describedby="auto-rebuild-hint" checked>' in text) is (
        checked
    )
    assert '{{' not in text
    assert script.content_type in {
        'text/javascript', 'application/javascript',
    }


async def test_settings_page_has_connection_map() -> None:
    """The Display form has the connection map radios, Changes checked."""
    client = await _client(display=WebDisplay(status=dict))
    try:
        text = await (await client.get('/settings')).text()
    finally:
        await client.close()
    assert re.search(
        r'<input type="radio" name="connection_map" value="changes" '
        r'checked aria-describedby="map-changes-hint">',
        text,
    )
    assert 'value="whole" aria-describedby' in text


def test_live_page_links_to_settings() -> None:
    """The Live page's header links to Settings."""
    web_dir = files('paperroute') / 'assets' / 'web'
    html = (web_dir / 'live.html').read_text(encoding='utf-8')
    assert ('/settings', False, 'Settings') in parse_page(text=html).links


DISPLAY_FORM = [
    ('peak_rate', '60s'),
    ('offpeak_rate', '2min'),
    ('peak_1_start', '07:00'),
    ('peak_1_end', '09:00'),
    ('peak_2_start', ''),
    ('peak_2_end', ''),
    ('rotate', 'off'),
    ('rotate', 'on'),
    ('larger_text', 'off'),
    ('inverted_lists', 'off'),
]


async def test_display_form_posts_event(
    display: WebDisplay, client: Client
) -> None:
    """A form post of the Display section posts one event and redirects."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings', data=DISPLAY_FORM, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert posted == [SettingsChanged(
        peak_rate=Rate.STEADY,
        offpeak_rate=Rate.SLOW,
        peak_windows=(Window(start=clock(7), end=clock(9)),),
        rotate=True,
        larger_text=False,
        inverted_lists=False,
    )]


async def test_json_post_answers_no_content(
    display: WebDisplay, client: Client
) -> None:
    """A JSON post is answered 204."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post('/settings', json={'rotate': 'on'})
    assert response.status == HTTPStatus.NO_CONTENT
    assert posted == [SettingsChanged(rotate=True)]


async def test_bad_peak_redirects_with_error(
    display: WebDisplay, client: Client
) -> None:
    """A window ending before it starts posts nothing and shows an error."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings',
        data={'peak_1_start': '10:00', 'peak_1_end': '09:00'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings?error=peak'
    assert not posted
    page = await client.get('/settings?error=peak')
    text = await page.text()
    assert 'Peak hours not saved: each start must be before' in text
    assert 'Quiet hours not saved' not in text
    plain = await (await client.get('/settings')).text()
    assert 'not saved' not in plain


async def test_json_bad_time_is_422_with_the_error_text(
    display: WebDisplay, client: Client
) -> None:
    """A JSON post with an unusable time gets the error text to show."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings',
        json={
            'quiet_mode': 'my_hours',
            'quiet_start': '25:00',
            'quiet_end': '07:00',
        },
    )
    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert await response.json() == {'error': ERRORS['quiet']}
    assert not posted


async def test_form_bad_time_still_redirects(
    display: WebDisplay, client: Client
) -> None:
    """A form post with an unusable time goes back to the error line."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings',
        data={
            'quiet_mode': 'my_hours',
            'quiet_start': '25:00',
            'quiet_end': '07:00',
        },
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings?error=quiet'
    assert not posted


async def test_every_settings_card_has_its_own_save(client: Client) -> None:
    """Each card's form has a visible Save and a note for the script."""
    text = await (await client.get('/settings')).text()
    forms = [
        chunk.split('</form>')[0]
        for chunk in text.split('<form')[1:]
    ]
    assert len(forms) == 4
    for form in forms:
        assert 'action="/settings"' in form
        buttons = re.findall(r'<button[^>]*type="submit"[^>]*>', form)
        assert len(buttons) == 1
        assert 'hidden' not in buttons[0]
        assert form.count('class="hint saved"') == 1
    assert 'timetable-save' not in text
    assert 'settings-status' not in text


async def test_unknown_value_is_400(
    display: WebDisplay, client: Client
) -> None:
    """A rate the page never sends is refused."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post('/settings', data={'peak_rate': '1s'})
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted


async def test_unknown_lookahead_is_400(
    display: WebDisplay, client: Client
) -> None:
    """A lookahead the page never sends is refused."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings', data={'alert_lookahead': '2w'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not posted


async def test_alerts_section_posts_event(
    display: WebDisplay, client: Client
) -> None:
    """A form post of the Alerts section posts one event and redirects."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings',
        data=[('alert_lookahead', 'all')],
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert posted == [SettingsChanged(alert_lookahead=Lookahead.ALL)]


async def test_settings_cross_site_403(
    display: WebDisplay, client: Client
) -> None:
    """A post from another site is refused."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/settings',
        data={'rotate': 'on'},
        headers={'Origin': 'http://evil.example'},
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not posted


async def test_slot_two_save_overlay_clears() -> None:
    """Only Afternoon filled: the app reports it as the first slot."""
    status: dict[str, object] = {'settings': {'peak_1_start': '06:30'}}
    display = WebDisplay(status=lambda: status)
    client = await _client(display=display)
    fields = {
        'peak_1_start': '', 'peak_1_end': '',
        'peak_2_start': '15:00', 'peak_2_end': '19:00',
    }
    try:
        await client.post('/settings', data=fields)
        shown = await (await client.get('/settings')).text()
        assert 'name="peak_1_start" value="15:00"' in shown
        status['settings'] = {
            'peak_1_start': '15:00', 'peak_1_end': '19:00',
            'peak_2_start': '', 'peak_2_end': '',
        }
        await client.get('/settings')
    finally:
        await client.close()
    assert not display._posted  # pylint: disable=protected-access


async def test_later_panel_change_shows_on_page() -> None:
    """A change at the panel after a save is not hidden by the overlay."""
    status: dict[str, object] = {'settings': {'rotate': 'off'}}
    display = WebDisplay(status=lambda: status)
    client = await _client(display=display)
    try:
        await client.post('/settings', data={'rotate': 'on'})
        status['settings'] = {'rotate': 'off', 'larger_text': 'on'}
        text = await (await client.get('/settings')).text()
    finally:
        await client.close()
    assert re.search(r'name="rotate"\s+value="on">', text)
    assert re.search(r'name="larger_text"\s+value="on" checked>', text)


async def test_page_shows_posted_before_status() -> None:
    """The posted value wins until the status reports it."""
    status: dict[str, object] = {'settings': {'rotate': 'off'}}
    display = WebDisplay(status=lambda: status)
    client = await _client(display=display)
    rotate = re.compile(r'name="rotate"\s+value="on" checked>')
    try:
        await client.post('/settings', data={'rotate': 'on'})
        shown = await (await client.get('/settings')).text()
        assert rotate.search(shown)
        assert display._posted == {'rotate': 'on'}  # pylint: disable=protected-access
        status['settings'] = {'rotate': 'on'}
        later = await (await client.get('/settings')).text()
    finally:
        await client.close()
    assert rotate.search(later)
    assert not display._posted  # pylint: disable=protected-access


class _Controls(HTMLParser):
    """Collect the controls outside forms and the unlabelled ones."""

    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.loose: list[str] = []
        self.forms: list[dict[str, str | None]] = []
        self.submits: list[int] = []
        self.label_depth = 0
        self.unlabelled: list[str] = []
        self.for_ids: set[str] = set()
        self.control_ids: list[tuple[str, str | None]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Track forms, labels and controls."""
        found = dict(attrs)
        if tag == 'form':
            self.in_form = True
            self.forms.append(found)
            self.submits.append(0)
        elif tag == 'label':
            self.label_depth += 1
            if found.get('for'):
                self.for_ids.add(str(found['for']))
        elif tag in {'input', 'select', 'button'}:
            self._control(tag=tag, found=found)

    def _control(self, *, tag: str, found: dict[str, str | None]) -> None:
        """Record one control."""
        kind = found.get('type')
        if tag == 'input' and kind == 'hidden':
            return
        if not self.in_form:
            self.loose.append(tag)
        if tag == 'button' and kind == 'submit':
            self.submits[-1] += 1
        if tag != 'button' and not self.label_depth:
            self.control_ids.append((tag, found.get('id')))

    def handle_endtag(self, tag: str) -> None:
        """Close forms and labels."""
        if tag == 'form':
            self.in_form = False
        elif tag == 'label':
            self.label_depth -= 1


async def test_page_works_without_script() -> None:
    """Every control is in a post form with Save; each has a label."""
    display = WebDisplay()
    client = await _client(display=display)
    try:
        text = await (await client.get('/settings')).text()
    finally:
        await client.close()
    parser = _Controls()
    parser.feed(text)
    assert not parser.loose
    assert len(parser.forms) == 4
    assert all(form['method'] == 'post' for form in parser.forms)
    assert all(form['action'] == '/settings' for form in parser.forms)
    assert all(count >= 1 for count in parser.submits)
    for tag, ident in parser.control_ids:
        assert ident in parser.for_ids, tag


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
    assert form.headers['Location'] == '/device?power=poweroff'
    assert posted == [
        PowerRequested(action=PowerAction.RESTART),
        PowerRequested(action=PowerAction.OFF),
    ]


@pytest.mark.parametrize(('action', 'question', 'button'), [
    (
        'poweroff',
        "Shut down now? The screen will show OFF when it's safe to unplug.",
        'Shut down',
    ),
    ('reboot', 'Restart now?', 'Restart'),
])
async def test_power_works_without_script(
    display: WebDisplay,
    client: Client,
    action: str,
    question: str,
    button: str,
) -> None:
    """Button, confirm page, its form, then the page saying so."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    confirm = await client.post(f'/device/power?action={action}')
    text = await confirm.text()
    assert f'<p>{question}</p>' in text
    assert f'name="action" value="{action}"' in text
    assert f'>{button}</button>' in text
    assert '<a class="button secondary" href="/device">Cancel</a>' in text
    assert '{{' not in text
    assert not posted
    done = await client.post(
        '/power', data={'action': action}, allow_redirects=False,
    )
    assert done.headers['Location'] == f'/device?power={action}'
    assert posted == [PowerRequested(action=PowerAction(action))]


async def test_power_page_rejects_unknown(client: Client) -> None:
    """The confirm page needs a known action."""
    response = await client.get('/device/power?action=halt')
    assert response.status == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize('path', ['/power', '/device/power'])
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


@pytest.mark.parametrize(('method', 'path'), POWER_ROUTES)
async def test_no_power_without_device_pages(method: str, path: str) -> None:
    """Without device pages, as in replay, power is not served."""
    client = await _client(display=WebDisplay())
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.NOT_FOUND
    finally:
        await client.close()


async def test_web_wake_keeps_the_map_view(
    display: WebDisplay, client: Client
) -> None:
    """The live calls that woke the Pi wake it without leaving the Map."""
    posted: list[Event] = []
    display.bind(post=posted.append)
    turn = {'knob': 'top', 'action': 'turn', 'steps': 0}
    press = {'knob': 'bottom', 'action': 'press'}
    rejected = await client.post('/knob', json=turn)
    pressed = await client.post('/knob', json=press)
    state = replace(asleep_at_one(), view=LiveView.MAP)
    for event in posted:
        state, _ = step(state, event)
    assert rejected.status == HTTPStatus.BAD_REQUEST
    assert pressed.status == HTTPStatus.NO_CONTENT
    assert posted == [KnobPressed(knob=Knob.BOTTOM)]
    assert not state.panel.asleep
    assert state.view == LiveView.MAP


def test_settle_is_immediate(display: WebDisplay) -> None:
    """A browser copy is always settled."""
    assert display.settle(timeout=0)
