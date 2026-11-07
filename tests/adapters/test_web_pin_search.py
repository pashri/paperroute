"""Tests for the stop search page and its JSON route."""

from collections.abc import AsyncIterator
from http import HTTPStatus

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_board import SydneyPages
from paperroute.adapters.web_display import WebDisplay
from paperroute.core.pin_choices import StopMatch
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.core.model import RouteKind, StopPlace
from tests.adapters.test_web_device import Device

Client = TestClient[web.Request, web.Application]
PARRAMATTA = StopPlace(
    stop_id='P', name='Parramatta Station <1>', point=None,
)
MATCH = StopMatch(
    place=PARRAMATTA,
    kinds=(RouteKind.BUS, RouteKind.TRAIN),
    routes=('606', 'T1'),
)


@pytest.fixture(name='client')
async def _client() -> AsyncIterator[Client]:
    """
    A client of a display whose timetable finds one stop.

    Yields
    ------
    Client
        The client, closed after the test.
    """
    display = WebDisplay(
        device=Device().pages(),
        board=SydneyPages(
            pins=lambda: None,
            options=FakePinOptions(found=(MATCH,)),
            planner=None,
        ),
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    yield client
    await client.close()


async def test_search_page_shows_routes_under_name(client: Client) -> None:
    """Each result names the stop, then lists its routes on their own."""
    text = await (await client.get('/pins/new?q=parra')).text()
    name = text.index('<b>Parramatta Station &lt;1&gt;</b>')
    routes = text.index('<span class="routes">606 T1</span>')
    assert name < routes < text.index('</a>', name)


async def test_stops_json_shape(client: Client) -> None:
    """The JSON route gives id, name, kinds and routes."""
    response = await client.get('/pins/stops.json?q=parra')
    assert response.status == HTTPStatus.OK
    assert response.content_type == 'application/json'
    assert await response.json() == [{
        'id': 'P',
        'name': 'Parramatta Station <1>',
        'kinds': ['bus', 'train'],
        'routes': ['606', 'T1'],
    }]


async def test_stops_json_short_query_is_empty(client: Client) -> None:
    """Under two characters gives an empty list."""
    response = await client.get('/pins/stops.json?q=%20p')
    assert await response.json() == []


async def test_stops_json_before_timetable() -> None:
    """Without a timetable the list is empty."""
    display = WebDisplay(
        device=Device().pages(),
        board=SydneyPages(pins=lambda: None, options=None, planner=None),
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    response = await client.get('/pins/stops.json?q=parra')
    found = await response.json()
    await client.close()
    assert found == []


async def test_stop_search_script_is_served(client: Client) -> None:
    """The script is served as JavaScript."""
    response = await client.get('/stop-search.js')
    assert response.status == HTTPStatus.OK
    assert response.content_type == 'text/javascript'
    assert 'export function attachStopSearch' in await response.text()


async def test_search_follows_school_switch() -> None:
    """Both searches leave school routes out unless the setting is off."""
    options = FakePinOptions(found=(MATCH,))
    reported: dict[str, object] = {}
    display = WebDisplay(
        status=lambda: reported,
        board=SydneyPages(pins=lambda: None, options=options, planner=None),
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        await client.get('/pins/stops.json?q=parra')
        reported['settings'] = {'skip_school_buses': 'off'}
        await client.get('/pins/new?q=parra')
    finally:
        await client.close()
    assert options.skipped == [True, False]
