"""Shared fixtures for the web pins tests."""

from collections.abc import AsyncIterator

import pytest

from paperroute_transit.observers import PinsSeen
from tests.adapters.web_pins.helpers import (
    HOME,
    LOST,
    ODD,
    SAVED,
    WORK,
    Client,
    Pins,
    _client,
    _options,
)


@pytest.fixture(name='pins')
def _pins() -> Pins:
    """Three pins, the first on screen."""
    return Pins(pins=(WORK, HOME, ODD))


@pytest.fixture(name='client')
async def _web_client(pins: Pins) -> AsyncIterator[Client]:
    """A client of the pins page."""
    client = await _client(pins=pins)
    yield client
    await client.close()


@pytest.fixture(name='editor')
async def _editor(pins: Pins) -> AsyncIterator[Client]:
    """A client whose display has the fake timetable."""
    pins.seen = PinsSeen(pins=(SAVED, LOST), shown=0, revision=3)
    client = await _client(pins=pins, options=_options())
    yield client
    await client.close()
