"""Tests for the web Device page."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from http import HTTPStatus
from importlib.resources import files
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.device_page import ASKED as DEVICE_ASKED
from paperroute.adapters.device_page import ago_text
from paperroute.adapters.web_device import DevicePages
from paperroute.adapters.web_display import WebDisplay
from paperroute.core.device_model import (
    Confirm,
    ConfirmAction,
    DeviceFacts,
    DeviceStatus,
    Origin,
    WifiStatus,
)
from paperroute.core.feeds import FEEDS, POSITION_FEEDS
from paperroute.core.messages import (
    ApiKeyChanged,
    ConfirmAsked,
    Event,
    PowerAction,
    PowerRequested,
)
from paperroute.core.model import SYDNEY, Mode
from paperroute.core.panel_views import confirm_view
from tests.fakes import FakeKeys
from tests.page_markup import parse_page

Client = TestClient[web.Request, web.Application]
KEY = 'already-set-key-123'
NOW = 1_761_043_500.0  # 21 Oct 2025 21:45 in Sydney
TRAINS = POSITION_FEEDS[Mode.TRAIN][0]
BUSES = FEEDS[Mode.BUS]
ASKED_AT = datetime(2025, 10, 21, 21, 45, tzinfo=SYDNEY)
FACTS = DeviceFacts(
    wifi=WifiStatus(managed=True, ssid='Home', dbm=-52, ip='192.168.1.40'),
    clock_synced=True,
    uptime_s=2 * 3600 + 14 * 60 + 5,
    cpu_c=48.2,
    disk_free=21_400_000_000,
    mem_free=134_000_000,
    version='abc1234',
    last_error=None,
    tiles_built='2025-10-19T08:00:00+11:00',
    key_set=True,
)
STATUS = DeviceStatus(
    timetable='2025-10-21T21:45:00+11:00',
    schema='6',
    rebuilding=False,
    rebuild_failed=False,
    calls_today=8412,
    key_set=True,
    facts=FACTS,
)
ASKED = {
    'join_wifi': (
        'Confirm on the device. Bottom: press to start \u2022 '
        'Top: press to cancel'
    ),
    'reset_settings': (
        'Confirm on the device. Bottom: press to reset \u2022 '
        'Top: press to cancel'
    ),
    'erase': (
        'Confirm on the device. Bottom: press to erase \u2022 '
        'Top: press to cancel'
    ),
}


class Device:
    """
    A Device page over a status, feeds, a key store and files.

    Attributes
    ----------
    status : DeviceStatus | None
        What the page reads.
    feeds : dict[str, float]
        When each feed last worked.
    keys : FakeKeys
        The key store.
    posted : list[Event]
        Events posted so far.
    error_log : Path | None
        The error log.
    state_file : Path | None
        The saved state.
    """

    def __init__(self) -> None:
        self.status: DeviceStatus | None = STATUS
        self.feeds = {TRAINS: NOW - 12, BUSES: NOW - 40}
        self.keys = FakeKeys(key=KEY)
        self.posted: list[Event] = []
        self.error_log: Path | None = None
        self.state_file: Path | None = None

    def pages(self) -> DevicePages:
        """
        Build the pages over this fixture.

        Returns
        -------
        DevicePages
            The pages.
        """
        return DevicePages(
            status=lambda: self.status,
            feeds=lambda: self.feeds,
            key=self.keys,
            post=self.posted.append,
            error_log=self.error_log,
            state_file=self.state_file,
            clock=lambda: NOW,
        )


@pytest.fixture(name='device')
def _device() -> Device:
    """A device with a status, two feeds and a key."""
    return Device()


async def _start(*, device: Device) -> tuple[Client, WebDisplay]:
    """Start a client of a display serving the Device page."""
    display = WebDisplay(device=device.pages())
    display.bind(post=device.posted.append)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client, display


@pytest.fixture(name='client')
async def _client(device: Device) -> AsyncIterator[Client]:
    """A client of a display serving the Device page."""
    client, _ = await _start(device=device)
    yield client
    await client.close()


def _row(*, label: str, value: str) -> str:
    """The markup of one labelled row."""
    return f'<dt>{label}</dt>\n          <dd>{value}</dd>'


async def test_device_page_sections(client: Client) -> None:
    """Every section and row is drawn from the status and facts."""
    page = await client.get('/device')
    text = await page.text()
    assert page.content_type == 'text/html'
    for heading in (
        'Network', 'Data', 'System', 'TfNSW API key', 'Device', 'Reset',
    ):
        assert f'>{heading}</h2>' in text
    for label, value in (
        ('Wi-Fi', 'Home · strong (-52 dBm)'),
        ('Address', '192.168.1.40 · paperroute.local'),
        ('Clock', 'Synced'),
        ('Timetable', 'version 6 · built 21 Oct 21:45 · ok'),
        ('Map tiles', 'built 19 Oct'),
        ('API calls today', '8,412 of 60,000'),
        ('Software', 'abc1234'),
        ('Up', '2 h 14 min'),
        ('CPU', '48 °C'),
        ('Free disk', '21 GB · memory 134 MB'),
        ('Last error', 'none'),
    ):
        assert _row(label=label, value=value) in text
    assert 'action="/device/wifi"' in text
    assert 'Join another network&hellip;' in text
    assert (
        'Confirm on the device. Hidden networks and a show-password '
        'toggle are on the setup page.'
    ) in text
    assert '<a href="/device/errors.jsonl">Download error log</a>' in text
    assert '<a href="/device/state.json">Download state</a>' in text
    assert 'No secrets in either file.' in text
    assert 'Key set · last used 12 s ago' in text
    assert 'name="api_key" type="password"' in text
    assert 'autocomplete="off"' in text
    assert '<button type="submit">Save key</button>' in text
    assert 'These ask on this page first, then act.' in text
    assert 'name="kind" value="settings"' in text
    assert 'name="kind" value="erase"' in text
    for label in ('Reset settings', 'Erase everything'):
        assert f'<button type="submit" class="danger">{label}</button>' in text
    assert (
        'Reset settings clears pins, settings, Wi-Fi and the key. Erase '
        'everything also deletes the timetable. The map is kept. Both are '
        'confirmed on the device.'
    ) in text
    assert '{{' not in text


@pytest.mark.parametrize(('change', 'label', 'value'), [
    ({'rebuilding': True}, 'Timetable', 'rebuilding'),
    ({'rebuild_failed': True, 'timetable': None}, 'Timetable',
     'last build failed'),
    ({'timetable': None}, 'Timetable', 'none yet'),
    ({'rebuild_failed': True}, 'Timetable',
     'version 6 · built 21 Oct 21:45 · last build failed'),
    ({'facts': replace(FACTS, wifi=WifiStatus(managed=True))}, 'Wi-Fi',
     'not connected'),
    ({'facts': replace(FACTS, wifi=WifiStatus(managed=True))}, 'Address',
     'paperroute.local'),
    ({'facts': replace(FACTS, tiles_built=None)}, 'Map tiles', 'none'),
    ({'facts': replace(FACTS, uptime_s=600)}, 'Up', '10 min'),
    ({'facts': replace(FACTS, clock_synced=False)}, 'Clock', 'Not synced'),
    ({'facts': replace(FACTS, clock_synced=None)}, 'Clock', 'Unknown'),
    ({'facts': None}, 'Clock', 'Unknown'),
    ({'facts': replace(FACTS, last_error='9:01 <boom>')}, 'Last error',
     '9:01 &lt;boom&gt;'),
    ({'facts': None}, 'Wi-Fi', '-'),
])
async def test_device_page_values(
    device: Device,
    client: Client,
    change: dict[str, object],
    label: str,
    value: str,
) -> None:
    """Each row says what is known, escaped."""
    device.status = replace(STATUS, **change)  # type: ignore[arg-type]
    text = await (await client.get('/device')).text()
    assert _row(label=label, value=value) in text


async def test_device_page_feed_table(client: Client) -> None:
    """Each feed shows its name, when it last worked and how long ago."""
    text = await (await client.get('/device')).text()
    assert '<caption>Feeds</caption>' in text
    for column in ('Feed', 'Last ok', 'Age'):
        assert f'<th scope="col">{column}</th>' in text
    assert (
        '<th scope="row">Sydney Trains positions</th>'
        '<td>21:44:48</td><td>12 s</td>'
    ) in text
    assert (
        '<th scope="row">Buses trip updates</th>'
        '<td>21:44:20</td><td>40 s</td>'
    ) in text



@pytest.mark.parametrize(('seconds', 'shown'), [
    (-5.0, '0 s'),
    (59.9, '59 s'),
    (60.0, '1 min'),
    (3_599.0, '59 min'),
    (7_300.0, '2 h'),
])
def test_ago_text_units(seconds: float, shown: str) -> None:
    """Ages clamp at 0 and grow from seconds to minutes to hours."""
    assert ago_text(seconds=seconds) == shown

@pytest.mark.parametrize(('key', 'feeds', 'shown'), [
    (KEY, {}, 'Key set · not used yet'),
    (None, {}, 'No key set'),
    (KEY, {TRAINS: NOW - 300}, 'Key set · last used 5 min ago'),
])
async def test_device_page_key_states(
    device: Device,
    client: Client,
    key: str | None,
    feeds: dict[str, float],
    shown: str,
) -> None:
    """The key row says whether a key is set and when it was used."""
    device.keys.key = key
    device.feeds = feeds
    assert shown in await (await client.get('/device')).text()


async def test_device_page_never_shows_key(client: Client) -> None:
    """The key in the store is never drawn into the page."""
    text = await (await client.get('/device')).text()
    assert KEY not in text


async def test_join_another_network_asks_device(
    device: Device, client: Client
) -> None:
    """The phone asks; the panel confirms."""
    response = await client.post('/device/wifi', allow_redirects=False)
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/device?asked=join_wifi'
    assert device.posted == [ConfirmAsked(action=ConfirmAction.JOIN_WIFI)]
    text = await (await client.get('/device?asked=join_wifi')).text()
    assert ASKED['join_wifi'] in text


@pytest.mark.parametrize(('kind', 'action'), [
    ('settings', ConfirmAction.RESET_SETTINGS),
    ('erase', ConfirmAction.ERASE),
])
async def test_reset_asks_device(
    device: Device, client: Client, kind: str, action: ConfirmAction
) -> None:
    """Each reset asks the panel and says so."""
    response = await client.post(
        '/device/reset', data={'kind': kind}, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == f'/device?asked={action.value}'
    assert device.posted == [ConfirmAsked(action=action)]
    text = await (await client.get(f'/device?asked={action.value}')).text()
    assert ASKED[action.value] in text


async def test_reset_unknown_kind_400(
    device: Device, client: Client
) -> None:
    """An unknown kind asks nothing."""
    response = await client.post('/device/reset', data={'kind': 'all'})
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not device.posted


async def test_asked_ignores_unknown_value(client: Client) -> None:
    """Only the three asks are drawn."""
    text = await (await client.get('/device?asked=restart')).text()
    assert 'Confirm on the device. Bottom:' not in text


@pytest.mark.parametrize('action', [
    ConfirmAction.JOIN_WIFI,
    ConfirmAction.RESET_SETTINGS,
    ConfirmAction.ERASE,
])
def test_device_asks_name_the_confirm_buttons(action: ConfirmAction) -> None:
    """The phone's ask names the buttons the panel draws."""
    confirm = Confirm(action=action, origin=Origin.PHONE, asked_at=ASKED_AT)
    view = confirm_view(confirm=confirm)
    assert view.confirm in DEVICE_ASKED[action.value]
    assert view.cancel in DEVICE_ASKED[action.value]


async def test_key_post_saves_and_posts(
    device: Device, client: Client
) -> None:
    """A good key replaces the old one and is never echoed."""
    new = 'abcdefghij0123456789'
    response = await client.post(
        '/device/key', data={'api_key': f' {new} '}, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/device?key=saved'
    assert device.keys.key == new
    assert device.posted == [ApiKeyChanged(present=True)]
    text = await (await client.get('/device?key=saved')).text()
    assert 'Key saved.' in text
    assert new not in text


async def test_key_post_bad_400(device: Device, client: Client) -> None:
    """A bad key is refused and the old one kept."""

    def refuse(*, key: str) -> None:
        del key
        raise ValueError('not a valid API key')

    device.keys.write = refuse  # type: ignore[method-assign]
    response = await client.post('/device/key', data={'api_key': 'x y'})
    text = await response.text()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert "Not saved: that doesn&#x27;t look like a TfNSW API key." in text
    assert 'x y' not in text
    assert device.keys.key == KEY
    assert not device.posted


async def test_downloads_serve_files(
    device: Device, tmp_path: Path
) -> None:
    """Both files download as attachments, never cached."""
    device.error_log = tmp_path / 'errors.jsonl'
    device.error_log.write_text('{"message": "x"}\n', encoding='utf-8')
    device.state_file = tmp_path / 'state.json'
    device.state_file.write_text('{"schema": 4}', encoding='utf-8')
    client, _ = await _start(device=device)
    try:
        for name, body, kind in (
            ('errors.jsonl', '{"message": "x"}\n', 'application/x-ndjson'),
            ('state.json', '{"schema": 4}', 'application/json'),
        ):
            response = await client.get(f'/device/{name}')
            assert response.status == HTTPStatus.OK
            assert response.content_type == kind
            assert await response.text() == body
            assert response.headers['Content-Disposition'] == (
                f'attachment; filename="{name}"'
            )
            assert response.headers['Cache-Control'] == 'no-store'
    finally:
        await client.close()


async def test_downloads_404_without_file(
    device: Device, client: Client, tmp_path: Path
) -> None:
    """No file given, or a missing one, is 404."""
    for name in ('errors.jsonl', 'state.json'):
        response = await client.get(f'/device/{name}')
        assert response.status == HTTPStatus.NOT_FOUND
    device.error_log = tmp_path / 'gone.jsonl'
    other, _ = await _start(device=device)
    try:
        response = await other.get('/device/errors.jsonl')
        assert response.status == HTTPStatus.NOT_FOUND
    finally:
        await other.close()


async def test_power_lives_on_device(
    device: Device, client: Client
) -> None:
    """Restart and Shut down moved from Settings to Device."""
    settings = await (await client.get('/settings')).text()
    assert 'Restart' not in settings
    assert '<dialog' not in settings
    text = await (await client.get('/device')).text()
    assert 'action="/device/power?action=reboot"' in text
    assert 'action="/device/power?action=poweroff"' in text
    assert '<p id="reboot-question">Restart now?</p>' in text
    assert (
        '<p id="poweroff-question">Shut down now? The screen will say '
        "when it's safe to unplug.</p>"
    ) in text
    assert text.count('<dialog ') == 2
    assert '<script type="module" src="/device.js"></script>' in text
    confirm = await client.post('/device/power?action=reboot')
    confirm_text = await confirm.text()
    assert '<p>Restart now?</p>' in confirm_text
    assert (
        '<a class="button secondary" href="/device">Cancel</a>'
    ) in confirm_text
    assert not device.posted
    done = await client.post(
        '/power', data={'action': 'reboot'}, allow_redirects=False,
    )
    assert done.status == HTTPStatus.SEE_OTHER
    assert done.headers['Location'] == '/device?power=reboot'
    after = await (await client.get('/device?power=reboot')).text()
    assert 'aria-live="polite">Restarting...</p>' in after
    assert device.posted == [PowerRequested(action=PowerAction.RESTART)]
    gone = await client.get('/settings/power?action=reboot')
    assert gone.status == HTTPStatus.NOT_FOUND
    script = await client.get('/device.js')
    assert script.content_type in {
        'text/javascript', 'application/javascript',
    }


@pytest.mark.parametrize('path', [
    '/device/wifi', '/device/reset', '/device/key', '/device/power',
])
async def test_cross_site_refused_on_device_posts(
    device: Device, client: Client, path: str
) -> None:
    """Another site's form can't post to the Device routes."""
    response = await client.post(
        f'{path}?action=reboot',
        data={'kind': 'erase', 'api_key': 'abcdefghij0123456789'},
        headers={'Origin': 'http://evil.example'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not device.posted
    assert device.keys.key == KEY


@pytest.mark.parametrize(('name', 'current'), [
    ('live.html', '/'),
    ('settings.html', '/settings'),
    ('power.html', '/device'),
    ('setup.html', None),
    ('device.html', '/device'),
])
def test_nav_on_every_page(name: str, current: str | None) -> None:
    """Every page links Live, Pins, Settings and Device, marking the open one."""
    web_dir = files('paperroute') / 'assets' / 'web'
    page = parse_page(text=(web_dir / name).read_text(encoding='utf-8'))
    assert page.navs == [{'aria-label': 'Pages'}]
    assert page.links == [
        (href, href == current, label)
        for href, label in (
            ('/', 'Live'),
            ('/pins', 'Pins'),
            ('/settings', 'Settings'),
            ('/device', 'Device'),
        )
    ]
