"""Shared fixtures for the web display tests."""

from collections.abc import AsyncIterator

import pytest

from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device_testkit.device_pages import DeviceSite
from paperroute_sydney.city import SYDNEY
from tests.adapters.test_web_device import sydney_site
from tests.adapters.transit_web import transit_display
from tests.adapters.web_display.helpers import Client, _client


@pytest.fixture(name='site')
def _site() -> DeviceSite:
    """Sydney's device pages, recording what they post."""
    return sydney_site()


@pytest.fixture(name='display')
def _display(site: DeviceSite) -> WebDisplay:
    """A display with device pages that has shown nothing yet."""
    return transit_display(device=site.pages(), city=SYDNEY)


@pytest.fixture(name='client')
async def _web_client(display: WebDisplay) -> AsyncIterator[Client]:
    """A client of the display's app."""
    client = await _client(display=display)
    yield client
    await client.close()


@pytest.fixture(name='posted')
def _posted(display: WebDisplay) -> list[object]:
    """The events the display posts, as they arrive."""
    events: list[object] = []
    display.bind(post=events.append)
    return events
