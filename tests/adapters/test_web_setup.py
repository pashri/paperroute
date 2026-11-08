"""Tests for the phone setup page as Sydney's web display serves it."""

from collections.abc import AsyncIterator
from dataclasses import replace
from http import HTTPStatus

import pytest
from aiohttp import ClientResponse, web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.tfnsw import TFNSW_KEY
from paperroute.adapters.web_display import WebDisplay
from paperroute.city import SYDNEY, SYDNEY_ZONE
from paperroute.sydney_feeds import DAILY_LIMIT, feed_label
from paperroute_testkit.device_pages import HOTSPOT, DeviceSite

Client = TestClient[web.Request, web.Application]


@pytest.fixture(name='setup')
def _setup() -> DeviceSite:
    """Sydney's pages over a running hotspot with a key already set."""
    return DeviceSite(
        status=HOTSPOT,
        feeds={},
        credential=TFNSW_KEY,
        feed_label=feed_label,
        daily_limit=DAILY_LIMIT,
        zone=SYDNEY_ZONE,
    )


@pytest.fixture(name='client')
async def _client(setup: DeviceSite) -> AsyncIterator[Client]:
    """A client of a display serving the setup page."""
    client = TestClient(
        TestServer(WebDisplay(device=setup.pages(), city=SYDNEY).app()),
    )
    await client.start_server()
    yield client
    await client.close()


async def _post(
    *, client: Client, data: dict[str, str]
) -> ClientResponse:
    """Post the setup form without following the redirect."""
    return await client.post('/setup', data=data, allow_redirects=False)


async def test_setup_page_asks_for_key_only_without_key(
    setup: DeviceSite, client: Client
) -> None:
    """The key field shows only on a device with no key."""
    page = await (await client.get('/setup')).text()
    assert 'name="api_key"' not in page
    assert setup.status is not None
    setup.status = replace(setup.status, key_set=False)
    page = await (await client.get('/setup')).text()
    assert 'name="api_key"' in page
    assert 'TfNSW API key' in page


async def test_setup_post_bad_key_no_join(
    setup: DeviceSite, client: Client
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


async def test_hotspot_address_is_allowed(setup: DeviceSite) -> None:
    """A phone on the hotspot reaches the page; other sites still can't."""
    display = WebDisplay(
        device=setup.pages(), remote_of=lambda request: '10.42.0.23',
        city=SYDNEY,
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
    client = TestClient(TestServer(WebDisplay(city=SYDNEY).app()))
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
    setup: DeviceSite, method: str, path: str
) -> None:
    """A peer outside the local network can't reach the setup page."""
    display = WebDisplay(
        device=setup.pages(), remote_of=lambda request: '8.8.8.8',
        city=SYDNEY,
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.FORBIDDEN
        assert not setup.posted
    finally:
        await client.close()

