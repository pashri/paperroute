"""Tests for the phone setup page."""

import html
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from http import HTTPStatus

import pytest
from aiohttp import ClientResponse, web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_device import DevicePages
from paperroute.adapters.web_display import WebDisplay
from paperroute.core.device_model import (
    DeviceStatus,
    JoinRequest,
    WifiNetwork,
    WifiPhase,
    WifiSetup,
)
from paperroute.core.messages import ApiKeyChanged, Event, JoinAsked
from tests.fakes import FakeKeys

Client = TestClient[web.Request, web.Application]
GOOD_KEY = 'abcdefghij0123456789'
NOT_RUNNING = (
    "Wi-Fi setup isn&#x27;t running. On the device, hold both knobs, "
    'then choose Wi-Fi.'
)
NETWORKS = (
    WifiNetwork(ssid='Home', signal=80, secured=True, enterprise=False),
    WifiNetwork(
        ssid='Cafe:Wi-Fi', signal=40, secured=False, enterprise=False,
    ),
    WifiNetwork(ssid='Corp', signal=20, secured=True, enterprise=True),
)
STATUS = DeviceStatus(
    timetable=None,
    schema=None,
    rebuilding=False,
    rebuild_failed=False,
    calls_today=0,
    key_set=True,
    facts=None,
    setup=WifiSetup(
        phase=WifiPhase.HOTSPOT, password='7Q4K9W2M', networks=NETWORKS,
    ),
)


class Setup:
    """
    A setup page with a status, a key store and the events posted.

    Attributes
    ----------
    status : DeviceStatus | None
        What the page reads.
    keys : FakeKeys
        The key store.
    posted : list[Event]
        Events posted so far.
    """

    def __init__(self) -> None:
        self.status: DeviceStatus | None = STATUS
        self.keys = FakeKeys(key='already-set-key-123')
        self.posted: list[Event] = []

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
            feeds=dict,
            key=self.keys,
            post=self.posted.append,
            error_log=None,
            state_file=None,
        )


@pytest.fixture(name='setup')
def _setup() -> Setup:
    """A running hotspot with a key already set."""
    return Setup()


@pytest.fixture(name='client')
async def _client(setup: Setup) -> AsyncIterator[Client]:
    """A client of a display serving the setup page."""
    client = TestClient(TestServer(WebDisplay(device=setup.pages()).app()))
    await client.start_server()
    yield client
    await client.close()


async def _post(
    *, client: Client, data: dict[str, str]
) -> ClientResponse:
    """Post the setup form without following the redirect."""
    return await client.post('/setup', data=data, allow_redirects=False)


async def test_setup_page_lists_networks(client: Client) -> None:
    """Each network is a radio; enterprise ones are disabled."""
    page = await (await client.get('/setup')).text()
    assert '<form method="post" action="/setup">' in page
    assert page.count('type="radio" name="network"') == 4
    assert 'value="Home"' in page
    assert 'Home (strong)' in page
    assert 'Cafe:Wi-Fi (good) (open)' in page
    assert 'Corp (weak) (not supported)' in page
    assert 'value="Corp" required disabled' in page
    assert 'value="__hidden__"' in page
    assert 'Hidden network' in page
    assert 'name="hidden_ssid"' in page
    assert 'type="password"' in page
    assert 'autocomplete="current-password"' in page
    assert 'Show password' in page
    assert '7Q4K9W2M' not in page


async def test_setup_escapes_ssid(setup: Setup, client: Client) -> None:
    """A network name is shown as text, never as markup."""
    assert setup.status is not None and setup.status.setup is not None
    tag = WifiNetwork(
        ssid='<b>x</b>', signal=90, secured=True, enterprise=False,
    )
    setup.status = replace(
        setup.status, setup=replace(setup.status.setup, networks=(tag,)),
    )
    page = await (await client.get('/setup')).text()
    assert '<b>x</b>' not in page
    assert html.escape('<b>x</b>') in page


async def test_setup_page_asks_for_key_only_without_key(
    setup: Setup, client: Client
) -> None:
    """The key field shows only on a device with no key."""
    page = await (await client.get('/setup')).text()
    assert 'name="api_key"' not in page
    assert setup.status is not None
    setup.status = replace(setup.status, key_set=False)
    page = await (await client.get('/setup')).text()
    assert 'name="api_key"' in page
    assert 'TfNSW API key' in page


@pytest.mark.parametrize('phase', [None, WifiPhase.SCANNING])
async def test_setup_page_when_not_running(
    setup: Setup, client: Client, phase: WifiPhase | None
) -> None:
    """With no hotspot up, the page says how to start one."""
    assert setup.status is not None
    setup.status = replace(
        setup.status,
        setup=None if phase is None else WifiSetup(phase=phase),
    )
    page = await (await client.get('/setup')).text()
    assert NOT_RUNNING in page
    assert '<form' not in page


async def test_setup_page_says_joining(client: Client) -> None:
    """After a post the page says what happens next."""
    page = await (await client.get('/setup?joining=1')).text()
    assert (
        'Joining... Your phone will drop off paperroute-setup. If the '
        'password is wrong, paperroute-setup comes back: join it again '
        'and try once more.'
    ) in page


async def test_setup_post_posts_join(setup: Setup, client: Client) -> None:
    """A chosen network and password are sent to be joined."""
    response = await _post(
        client=client, data={'network': 'Home', 'password': 'pw12345678'},
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/setup?joining=1'
    assert setup.posted == [
        JoinAsked(request=JoinRequest('Home', 'pw12345678', False)),
    ]


async def test_setup_post_hidden(setup: Setup, client: Client) -> None:
    """A hidden network's name comes from the text field."""
    response = await _post(client=client, data={
        'network': '__hidden__',
        'hidden_ssid': 'Secret',
        'password': '',
    })
    assert response.status == HTTPStatus.SEE_OTHER
    assert setup.posted == [
        JoinAsked(request=JoinRequest('Secret', '', True)),
    ]


@pytest.mark.parametrize('data', [
    {'network': 'Home', 'password': 'short'},
    {'network': 'Home', 'password': 'x' * 64},
    {'network': 'Home', 'password': 'caf\u00e9-latte'},
])
async def test_setup_post_rejects_short_password(
    setup: Setup, client: Client, data: dict[str, str]
) -> None:
    """A password WPA2 can't take is refused and nothing is joined."""
    response = await _post(client=client, data=data)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert 'Wi-Fi passwords are 8 to 63 characters.' in await response.text()
    assert not setup.posted


@pytest.mark.parametrize('data', [
    {'password': 'pw12345678'},
    {'network': '__hidden__', 'hidden_ssid': '', 'password': ''},
    {'network': '__hidden__', 'hidden_ssid': 'x' * 33, 'password': ''},
    {'network': 'a\nb', 'password': ''},
])
async def test_setup_post_rejects_bad_ssid(
    setup: Setup, client: Client, data: dict[str, str]
) -> None:
    """A missing or impossible network name is refused."""
    response = await _post(client=client, data=data)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert 'Choose a network' in await response.text()
    assert not setup.posted


async def test_setup_post_when_not_running(
    setup: Setup, client: Client
) -> None:
    """Without the hotspot up, a post is refused."""
    assert setup.status is not None
    setup.status = replace(setup.status, setup=None)
    response = await _post(
        client=client, data={'network': 'Home', 'password': 'pw12345678'},
    )
    assert response.status == HTTPStatus.CONFLICT
    assert NOT_RUNNING in await response.text()
    assert not setup.posted


async def test_setup_post_writes_key_first(
    setup: Setup, client: Client
) -> None:
    """A given key is stored and announced before the join."""
    assert setup.status is not None
    setup.status = replace(setup.status, key_set=False)
    response = await _post(client=client, data={
        'network': 'Home', 'password': 'pw12345678', 'api_key': GOOD_KEY,
    })
    assert response.status == HTTPStatus.SEE_OTHER
    assert setup.keys.key == GOOD_KEY
    assert [type(event) for event in setup.posted] == [
        ApiKeyChanged, JoinAsked,
    ]
    assert setup.posted[0] == ApiKeyChanged(present=True)


async def test_setup_post_bad_key_no_join(
    setup: Setup, client: Client
) -> None:
    """A key the store refuses stops the join."""
    assert setup.status is not None
    setup.status = replace(setup.status, key_set=False)

    def refuse(*, key: str) -> None:
        del key
        raise ValueError('not a valid API key')

    setup.keys.write = refuse  # type: ignore[method-assign]
    response = await _post(client=client, data={
        'network': 'Home', 'password': 'pw12345678', 'api_key': 'bad key',
    })
    assert response.status == HTTPStatus.BAD_REQUEST
    assert "That doesn&#x27;t look like a TfNSW API key." in (
        await response.text()
    )
    assert not setup.posted


async def test_setup_never_echoes_password(
    setup: Setup, client: Client
) -> None:
    """Neither a refusal nor a redirect carries the password or key."""
    for data in (
        {'network': 'Home', 'password': 'secret-pass-1'},
        {'network': 'Home', 'password': 'p\u00e9', 'api_key': GOOD_KEY},
        {'network': 'Home', 'password': 'secret-pass-1', 'api_key': 'k y'},
    ):
        response = await _post(client=client, data=data)
        body = await response.text()
        location = response.headers.get('Location', '')
        for secret in (data['password'], data.get('api_key', 'none')):
            assert secret not in body
            assert secret not in location


async def test_hotspot_address_is_allowed(setup: Setup) -> None:
    """A phone on the hotspot reaches the page; other sites still can't."""
    display = WebDisplay(
        device=setup.pages(), remote_of=lambda request: '10.42.0.23',
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        host = {'Host': '10.42.0.1'}
        response = await client.get('/setup', headers=host)
        assert response.status == HTTPStatus.OK
        response = await client.post(
            '/setup',
            data={'network': 'Home', 'password': 'pw12345678'},
            headers={**host, 'Origin': 'http://10.42.0.1'},
            allow_redirects=False,
        )
        assert response.status == HTTPStatus.SEE_OTHER
        response = await client.post(
            '/setup',
            data={'network': 'Home', 'password': 'pw12345678'},
            headers={**host, 'Origin': 'http://evil.example'},
            allow_redirects=False,
        )
        assert response.status == HTTPStatus.FORBIDDEN
    finally:
        await client.close()


async def test_no_setup_routes_without_device() -> None:
    """A display built without device pages has no setup page."""
    client = TestClient(TestServer(WebDisplay().app()))
    await client.start_server()
    try:
        response = await client.get('/setup')
        assert response.status == HTTPStatus.NOT_FOUND
    finally:
        await client.close()


@pytest.mark.parametrize(('method', 'path'), [
    ('GET', '/setup'),
    ('GET', '/setup.js'),
    ('POST', '/setup'),
])
async def test_setup_refuses_public_address(
    setup: Setup, method: str, path: str
) -> None:
    """A peer outside the local network can't reach the setup page."""
    display = WebDisplay(
        device=setup.pages(), remote_of=lambda request: '8.8.8.8',
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.FORBIDDEN
        assert not setup.posted
    finally:
        await client.close()


async def test_setup_post_keeps_existing_key(
    setup: Setup, client: Client
) -> None:
    """A key posted to a device that has one is ignored."""
    response = await _post(client=client, data={
        'network': 'Home', 'password': 'pw12345678', 'api_key': GOOD_KEY,
    })
    assert response.status == HTTPStatus.SEE_OTHER
    assert setup.keys.key == 'already-set-key-123'
    assert [type(event) for event in setup.posted] == [JoinAsked]


async def test_setup_post_refuses_enterprise(
    setup: Setup, client: Client
) -> None:
    """A scanned enterprise network is refused like its label says."""
    response = await _post(
        client=client, data={'network': 'Corp', 'password': 'pw12345678'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert 'That network is not supported.' in await response.text()
    assert not setup.posted


async def test_setup_post_key_save_fails(
    setup: Setup, client: Client
) -> None:
    """A key that can't be written is a 500 and no join."""

    def fail(*, key: str) -> None:
        del key
        raise OSError('disk full')

    assert setup.status is not None
    setup.status = replace(setup.status, key_set=False)
    setup.keys.write = fail  # type: ignore[method-assign]
    response = await _post(client=client, data={
        'network': 'Home', 'password': 'pw12345678', 'api_key': GOOD_KEY,
    })
    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "Couldn&#x27;t save the key on the device. Try again." in (
        await response.text()
    )
    assert not setup.posted


async def test_setup_radios_required(client: Client) -> None:
    """The browser asks for a network before posting."""
    page = await (await client.get('/setup')).text()
    assert len(re.findall(r'name="network" value="[^"]*" required', page)) == 4
