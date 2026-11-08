"""Tests for the Device page as Sydney's web display serves it."""

from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Final

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_frame import page_frame
from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import PowerAction
from paperroute_sydney.adapters.tfnsw import TFNSW_KEY
from paperroute_sydney.adapters.web_board import SYDNEY_NAV
from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE, SydneyFeed
from paperroute_sydney.sydney_feeds import (
    DAILY_LIMIT,
    FEEDS,
    POSITION_FEEDS,
    feed_label,
)
from paperroute_testkit.device_pages import KEY, NOW, DeviceSite
from paperroute_testkit.page_markup import parse_page
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_web import BOARD_WEB
from paperroute_transit.adapters.web_display import RESET_HINT, WebDisplay

Client = TestClient[web.Request, web.Application]
TRAINS = POSITION_FEEDS[SydneyFeed.TRAIN][0]
BUSES = FEEDS[SydneyFeed.BUS]
SHELL_PAGES: Final[frozenset[str]] = frozenset(
    {'device.html', 'power.html', 'setup.html', 'live.html'},
)


def sydney_site() -> DeviceSite:
    """
    Give the device pages as Sydney's app builds them.

    Returns
    -------
    DeviceSite
        Sydney's key, feeds, limit and zone over the fixed status.
    """
    return DeviceSite(
        feeds={TRAINS: NOW - 12, BUSES: NOW - 40},
        credential=TFNSW_KEY,
        feed_label=feed_label,
        daily_limit=DAILY_LIMIT,
        zone=SYDNEY_ZONE,
        nav=SYDNEY_NAV,
        reset_hint=RESET_HINT,
    )


@pytest.fixture(name='device')
def _device() -> DeviceSite:
    """Sydney's device pages over a status, two feeds and a key."""
    return sydney_site()


async def _start(*, device: DeviceSite) -> Client:
    """Start a client of a display serving the Device page."""
    client = TestClient(
        TestServer(WebDisplay(device=device.pages(), city=SYDNEY).app()),
    )
    await client.start_server()
    return client


@pytest.fixture(name='client')
async def _client(device: DeviceSite) -> AsyncIterator[Client]:
    """A client of a display serving the Device page."""
    client = await _start(device=device)
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
        '<p class="hint">Reset settings clears pins, settings, Wi-Fi and the '
        'key. Erase everything also deletes the timetable. The map is kept. '
        'Both are confirmed on the device.</p>'
    ) in text
    assert '{{' not in text


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


async def test_key_post_bad_400(device: DeviceSite, client: Client) -> None:
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


async def test_power_lives_on_device(
    device: DeviceSite, client: Client
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
        '<p id="poweroff-question">Shut down now? The screen will show '
        "OFF when it's safe to unplug.</p>"
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
    device: DeviceSite, client: Client, path: str
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
    text = (
        page_frame(name=name, nav=SYDNEY_NAV, current=current)
        if name in SHELL_PAGES
        else BoardHead(nav=SYDNEY_NAV).fill(
            page=(BOARD_WEB / name).read_text(encoding='utf-8'),
            current=current,
        )
    )
    page = parse_page(text=text)
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
