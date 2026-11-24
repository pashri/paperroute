"""Fixtures for the web page tests."""

from collections.abc import AsyncIterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.connection_factories import WORK
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.observers import PinsSeen
from tests.adapters.test_connection_editor import OPTIONS
from tests.adapters.test_web_device import sydney_site
from tests.adapters.transit_web import pin_pages, transit_display
from tests.adapters.web_pins.helpers import Pins

Client = TestClient[web.Request, web.Application]


@pytest.fixture(name='pins')
def _pins() -> Pins:
    """The Work connection, on screen."""
    pins = Pins(pins=())
    pins.seen = PinsSeen(pins=(WORK,), shown=0, revision=3)
    return pins


@pytest.fixture(name='client')
async def _client(pins: Pins) -> AsyncIterator[Client]:
    """A client of a display with the fake timetable."""
    async for client in _serve(pins=pins, options=OPTIONS):
        yield client


@pytest.fixture(name='untimed')
async def _untimed(pins: Pins) -> AsyncIterator[Client]:
    """A client of a display whose timetable is not ready."""
    async for client in _serve(pins=pins, options=None):
        yield client


async def _serve(
    *, pins: Pins, options: FakePinOptions | None,
) -> AsyncIterator[Client]:
    """Serve the pin pages over the fake pins and close afterwards."""
    board = pin_pages(pins=pins.get, options=options)
    board.pins_sleep = pins.sleep
    display = transit_display(
        device=sydney_site().pages(),
        board=board,
        city=SYDNEY,
    )
    display.bind(post=pins.posted.append)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    yield client
    await client.close()
