"""Tests for the web Device page."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.device_page import ASKED as DEVICE_ASKED
from paperroute_device.adapters.device_page import ago_text
from paperroute_device.adapters.web_device import RESET_MARK, DevicePages
from paperroute_device.adapters.web_live import LiveView
from paperroute_device.adapters.web_shell import web_app
from paperroute_device.core.app import KIT_RESET_HINT
from paperroute_device.core.device_events import (
    ApiKeyChanged,
    ConfirmAsked,
    PowerRequested,
)
from paperroute_device.core.device_model import (
    Confirm,
    ConfirmAction,
    Credential,
    DeviceKey,
    Origin,
    PowerAction,
    WifiStatus,
)
from paperroute_device.core.panel_views import KIT_CONFIRM_COPY, confirm_view
from paperroute_device_testkit.device_fakes import FakeKeys
from paperroute_device_testkit.device_pages import (
    FACTS,
    HOTSPOT,
    KEY,
    NOW,
    STATUS,
    ZONE,
    DeviceSite,
)
from paperroute_hardware.ports import PanelTimes

Client = TestClient[web.Request, web.Application]
GOOD_KEY = 'abcdefghij0123456789'
EVIL = {'Origin': 'http://evil.example'}
B_KEY = Credential(
    label='Second key',
    refusal='Bad second key.',
    not_saved='Second key not saved.',
)
ASKED_AT = datetime(2025, 10, 21, 21, 45, tzinfo=ZONE)
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


@pytest.fixture(name='device')
def _device() -> DeviceSite:
    """Device pages over a status and a key."""
    return DeviceSite()


async def _start(*, device: DeviceSite) -> Client:
    """Start a client of an app serving only the device pages."""
    app = web_app(
        live=LiveView(knobs=lambda event: None),
        device=device.pages(),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.fixture(name='client')
async def _client(device: DeviceSite) -> AsyncIterator[Client]:
    """A client of an app serving only the device pages."""
    client = await _start(device=device)
    yield client
    await client.close()


def _row(*, label: str, value: str) -> str:
    """The markup of one labelled row."""
    return f'<dt>{label}</dt>\n          <dd>{value}</dd>'


@pytest.mark.parametrize(('change', 'label', 'value'), [
    ({'facts': replace(FACTS, wifi=WifiStatus(managed=True))}, 'Wi-Fi',
     'not connected'),
    ({'facts': replace(FACTS, wifi=WifiStatus(managed=True))}, 'Address',
     'paperroute.local'),
    ({'facts': replace(FACTS, uptime_s=600)}, 'Up', '10 min'),
    ({'facts': replace(FACTS, clock_synced=False)}, 'Clock', 'Not synced'),
    ({'facts': replace(FACTS, clock_synced=None)}, 'Clock', 'Unknown'),
    ({'facts': None}, 'Clock', 'Unknown'),
    ({'facts': replace(FACTS, last_error='9:01 <boom>')}, 'Last error',
     '9:01 &lt;boom&gt;'),
    ({'facts': None}, 'Wi-Fi', '-'),
])
async def test_device_page_values(
    device: DeviceSite,
    client: Client,
    change: dict[str, object],
    label: str,
    value: str,
) -> None:
    """Each row says what is known, escaped."""
    device.status = replace(STATUS, **change)  # type: ignore[arg-type]
    text = await (await client.get('/device')).text()
    assert _row(label=label, value=value) in text


@pytest.mark.parametrize(('panel', 'value'), [
    (PanelTimes(partial_s=0.516, full_s=4.81), 'partial 0.52 s, full 4.81 s'),
    (PanelTimes(full_s=4.81), 'full 4.81 s'),
    (PanelTimes(partial_s=0.5), 'partial 0.50 s'),
    (PanelTimes(), 'waiting for first refresh'),
    (PanelTimes(partial_s=0.5, ok=False), 'not answering'),
])
async def test_device_page_panel_line(
    device: DeviceSite, client: Client, panel: PanelTimes, value: str,
) -> None:
    """The Panel line says what the panel has done, or that it is silent."""
    device.panel = panel
    text = await (await client.get('/device')).text()
    assert _row(label='Panel', value=value) in text


async def test_device_page_without_panel_has_no_line(client: Client) -> None:
    """No panel, no Panel line."""
    text = await (await client.get('/device')).text()
    assert '<dt>Panel</dt>' not in text


@pytest.mark.parametrize(('knobs', 'value'), [
    (True, 'on'),
    (False, 'not answering'),
])
async def test_device_page_knobs_line(
    device: DeviceSite, client: Client, knobs: bool, value: str,
) -> None:
    """The Knobs line says whether the knobs' lines opened."""
    device.knobs = knobs
    text = await (await client.get('/device')).text()
    assert _row(label='Knobs', value=value) in text


async def test_device_page_without_knobs_has_no_line(client: Client) -> None:
    """No --knobs, no Knobs line."""
    text = await (await client.get('/device')).text()
    assert '<dt>Knobs</dt>' not in text


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

@pytest.mark.parametrize(('key', 'used', 'shown'), [
    (KEY, None, 'Key set · not used yet'),
    (None, None, 'No key set'),
    (KEY, NOW - 300, 'Key set · last used 5 min ago'),
])
async def test_device_page_key_states(
    device: DeviceSite,
    client: Client,
    key: str | None,
    used: float | None,
    shown: str,
) -> None:
    """The key row says whether a key is set and when it was used."""
    device.keys.key = key
    device.last_used = used
    assert shown in await (await client.get('/device')).text()


async def test_key_line_reads_the_keys_last_used() -> None:
    """Each key's line says when that key was used."""
    site, second = two_keys()
    site.keys.key, second.key = KEY, KEY
    site.last_used = NOW - 600
    client = await _start(device=site)
    try:
        text = await (await client.get('/device')).text()
    finally:
        await client.close()
    assert 'Key set · last used 10 min ago' in text
    assert 'Key set · last used 2 min ago' in text
    assert 'last used 5 s ago' not in text


async def test_device_page_never_shows_key(client: Client) -> None:
    """The key in the store is never drawn into the page."""
    text = await (await client.get('/device')).text()
    assert KEY not in text


async def test_join_another_network_asks_device(
    device: DeviceSite, client: Client
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
    device: DeviceSite, client: Client, kind: str, action: ConfirmAction
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
    device: DeviceSite, client: Client
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
    view = confirm_view(confirm=confirm, copy=KIT_CONFIRM_COPY)
    assert view.confirm in DEVICE_ASKED[action.value]
    assert view.cancel in DEVICE_ASKED[action.value]


async def test_key_post_saves_and_posts(
    device: DeviceSite, client: Client
) -> None:
    """A good key replaces the old one and is never echoed."""
    new = 'abcdefghij0123456789'
    response = await client.post(
        '/device/key', data={'api_key': f' {new} '}, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/device?key=saved'
    assert device.keys.key == new
    assert device.posted == [ApiKeyChanged(missing=())]
    text = await (await client.get('/device?key=saved')).text()
    assert 'Key saved.' in text
    assert new not in text


async def test_device_page_uses_credential_label(device: DeviceSite) -> None:
    """The page names the key and refuses it in the app's words."""
    device.credential = Credential(
        label='Test key',
        refusal='Bad test key.',
        not_saved='Test key not saved.',
    )

    def refuse(*, key: str) -> None:
        del key
        raise ValueError('not a valid API key')

    device.keys.write = refuse  # type: ignore[method-assign]
    client = await _start(device=device)
    try:
        page = await (await client.get('/device')).text()
        response = await client.post('/device/key', data={'api_key': 'short'})
        refused = await response.text()
    finally:
        await client.close()
    assert 'Test key' in page
    assert 'TfNSW' not in page
    assert response.status == HTTPStatus.BAD_REQUEST
    assert 'Test key not saved.' in refused


async def test_device_page_without_data_has_no_section(
    device: DeviceSite, client: Client
) -> None:
    """With no data callable the page has no Data section and no gap."""
    assert device.data is None
    text = await (await client.get('/device')).text()
    assert 'id="data"' not in text
    assert '{{' not in text
    assert '</section>\n    <section class="card setting" aria-labelledby="system">' in text


async def test_data_section_is_app_markup(device: DeviceSite) -> None:
    """The app's markup fills the page where the Data section was."""
    device.data = lambda: (
        '<section id="mine">\n      <p>{{system}} 1 < 2</p>\n    </section>'
    )
    client = await _start(device=device)
    try:
        text = await (await client.get('/device')).text()
    finally:
        await client.close()
    assert (
        '    <section id="mine">\n      <p>{{system}} 1 < 2</p>\n'
        '    </section>\n    <section class="card setting" '
        'aria-labelledby="system">'
    ) in text


async def test_downloads_serve_files(
    device: DeviceSite, tmp_path: Path
) -> None:
    """Both files download as attachments, never cached."""
    device.error_log = tmp_path / 'errors.jsonl'
    device.error_log.write_text('{"message": "x"}\n', encoding='utf-8')
    device.state_file = tmp_path / 'state.json'
    device.state_file.write_text('{"schema": 4}', encoding='utf-8')
    client = await _start(device=device)
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
    device: DeviceSite, client: Client, tmp_path: Path
) -> None:
    """No file given, or a missing one, is 404."""
    for name in ('errors.jsonl', 'state.json'):
        response = await client.get(f'/device/{name}')
        assert response.status == HTTPStatus.NOT_FOUND
    device.error_log = tmp_path / 'gone.jsonl'
    other = await _start(device=device)
    try:
        response = await other.get('/device/errors.jsonl')
        assert response.status == HTTPStatus.NOT_FOUND
    finally:
        await other.close()


async def test_device_pages_serve_power_page(
    device: DeviceSite, client: Client
) -> None:
    """The device pages alone confirm a shutdown and post it."""
    page = await client.get('/device/power?action=poweroff')
    assert page.status == HTTPStatus.OK
    assert 'Shut down now?' in await page.text()
    assert not device.posted
    done = await client.post(
        '/power', data={'action': 'poweroff'}, allow_redirects=False,
    )
    assert done.status == HTTPStatus.SEE_OTHER
    assert done.headers['Location'] == '/device?power=poweroff'
    assert device.posted == [PowerRequested(action=PowerAction.OFF)]


async def test_power_post_from_script_is_204(
    device: DeviceSite, client: Client
) -> None:
    """The page's script gets no redirect."""
    done = await client.post('/power', json={'action': 'reboot'})
    assert done.status == HTTPStatus.NO_CONTENT
    assert device.posted == [PowerRequested(action=PowerAction.RESTART)]


@pytest.mark.parametrize(('method', 'path'), [
    ('GET', '/device/power?action=halt'),
    ('POST', '/power'),
])
async def test_power_unknown_action_400(
    device: DeviceSite, client: Client, method: str, path: str
) -> None:
    """An unknown power action is refused and nothing is posted."""
    response = await client.request(method, path, data={'action': 'halt'})
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not device.posted


async def test_device_pages_default_nav_is_kit_nav() -> None:
    """Without an app's nav the pages link only Live and Device."""
    client = await _start(device=DeviceSite())
    try:
        page = await (await client.get('/device')).text()
    finally:
        await client.close()
    assert 'href="/"' in page
    assert 'href="/device"' in page
    assert 'href="/pins"' not in page


def two_keys() -> tuple[DeviceSite, FakeKeys]:
    """
    Give device pages with keys `a` and `b`.

    Returns
    -------
    tuple[DeviceSite, FakeKeys]
        The site, whose own store is key `a`'s, and key `b`'s store.
    """
    second = FakeKeys()
    site = DeviceSite(
        keys=FakeKeys(),
        key_name='a',
        more_keys=(
            DeviceKey(
                name='b',
                credential=B_KEY,
                store=second,
                display_name='B',
                last_used=lambda: NOW - 120,
            ),
        ),
    )
    return site, second


@pytest.mark.parametrize(('form', 'headers', 'status', 'a', 'b'), [
    ({'key': 'b'}, {}, HTTPStatus.SEE_OTHER, None, GOOD_KEY),
    ({}, {}, HTTPStatus.SEE_OTHER, GOOD_KEY, None),
    ({'key': 'a'}, {}, HTTPStatus.SEE_OTHER, GOOD_KEY, None),
    ({'key': 'zz'}, {}, HTTPStatus.BAD_REQUEST, None, None),
    ({'key': 'b'}, EVIL, HTTPStatus.FORBIDDEN, None, None),
])
async def test_second_key_saved_by_name(
    form: dict[str, str],
    headers: dict[str, str],
    status: HTTPStatus,
    a: str | None,
    b: str | None,
) -> None:
    """A post writes only the key it names, the first when it names none."""
    site, second = two_keys()
    client = await _start(device=site)
    try:
        response = await client.post(
            '/device/key',
            data={**form, 'api_key': GOOD_KEY},
            headers=headers,
            allow_redirects=False,
        )
    finally:
        await client.close()
    assert response.status == status
    assert (site.keys.key, second.key) == (a, b)


async def test_second_key_saved_posts_missing_keys() -> None:
    """Saving a further key posts which keys are still missing."""
    site, _ = two_keys()
    started = await _start(device=site)
    try:
        await started.post(
            '/device/key',
            data={'key': 'b', 'api_key': GOOD_KEY},
            allow_redirects=False,
        )
        await started.post(
            '/device/key',
            data={'key': 'a', 'api_key': GOOD_KEY},
            allow_redirects=False,
        )
    finally:
        await started.close()
    assert site.posted == [
        ApiKeyChanged(missing=('Test',)),
        ApiKeyChanged(missing=()),
    ]


async def test_second_key_refused_in_its_own_words() -> None:
    """A bad further key is refused with that key's copy."""
    site, _ = two_keys()
    client = await _start(device=site)
    try:
        response = await client.post(
            '/device/key', data={'key': 'b', 'api_key': ''},
        )
        refused = await response.text()
    finally:
        await client.close()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert 'Second key not saved.' in refused


async def test_two_keys_render_two_sections() -> None:
    """Each key has its own form; the second's ids end in its name."""
    site, _ = two_keys()
    client = await _start(device=site)
    try:
        text = await (await client.get('/device')).text()
    finally:
        await client.close()
    assert text.count('action="/device/key"') == 2
    assert text.count('id="api-key"') == 1
    assert 'id="api-key-b"' in text
    assert 'id="api-key-heading-b">Second key</h2>' in text
    assert '<input type="hidden" name="key" value="b">' in text
    assert text.count('name="key"') == 1


async def test_device_pages_with_no_keys() -> None:
    """With no keys there is no key card, no setup key field, no key post."""
    site = DeviceSite(status=replace(HOTSPOT, missing_keys=('Test',)))
    pages = DevicePages(
        status=lambda: site.status,
        panel=lambda: None,
        post=site.posted.append,
        zone=ZONE,
        error_log=None,
        state_file=None,
    )
    app = web_app(live=LiveView(knobs=lambda event: None), device=pages)
    client: Client = TestClient(TestServer(app))
    await client.start_server()
    try:
        device = await (await client.get('/device')).text()
        setup = await (await client.get('/setup')).text()
        response = await client.post(
            '/device/key', data={'api_key': GOOD_KEY}, allow_redirects=False,
        )
    finally:
        await client.close()
    assert 'action="/device/key"' not in device
    assert '<form method="post" action="/setup">' in setup
    assert 'name="api_key"' not in setup
    assert response.status == HTTPStatus.NOT_FOUND
    assert not site.posted


async def test_reset_hint_is_a_parameter() -> None:
    """The reset wording is the kit's own unless the app gives its own."""
    texts = []
    for site in (DeviceSite(), DeviceSite(reset_hint='Wipes <it> all.')):
        client = await _start(device=site)
        try:
            texts.append(await (await client.get('/device')).text())
        finally:
            await client.close()
    assert f'<p class="hint">{KIT_RESET_HINT}</p>' in texts[0]
    assert '<p class="hint">Wipes &lt;it&gt; all.</p>' in texts[1]
    assert KIT_RESET_HINT not in texts[1]
    assert RESET_MARK not in texts[0] + texts[1]


async def test_error_with_reset_mark_stays_text(
    device: DeviceSite, client: Client,
) -> None:
    """A last error reading like the reset mark is shown as written."""
    device.status = replace(
        STATUS, facts=replace(FACTS, last_error=RESET_MARK),
    )
    text = await (await client.get('/device')).text()
    assert _row(label='Last error', value=RESET_MARK) in text
    assert text.count(KIT_RESET_HINT) == 1
