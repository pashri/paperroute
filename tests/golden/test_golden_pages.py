"""The web pages, refusals and routes keep today's text."""

from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.board_pages import PinPages
from paperroute.adapters.settings_form import form_values
from paperroute.adapters.tfnsw import TFNSW_KEY
from paperroute.adapters.web_board import SYDNEY_HINT, SYDNEY_NAV, SuggestPlug
from paperroute.adapters.web_display import WebDisplay
from paperroute.city import SYDNEY, SYDNEY_ZONE
from paperroute.sydney_feeds import DAILY_LIMIT, feed_label
from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_device.adapters.web_device import DevicePages
from paperroute_device.core.device_model import DeviceKey, DeviceStatus
from paperroute_device.ports import KeyStore
from paperroute_testkit.device_fakes import FakeKeys
from paperroute_testkit.device_pages import HOTSPOT, KEY, NOW, STATUS
from paperroute_testkit.fakes import FakeTripPlanner
from tests.adapters.test_web_device import BUSES, TRAINS
from tests.adapters.test_web_pins import HOME, ODD, WORK, Pins
from tests.adapters.test_web_suggest import NOW as SEARCH_NOW
from tests.adapters.test_web_suggest import PLATFORMS
from tests.golden.scenes import GOLDEN, baseline_bytes
from tests.golden.test_golden_state import RICH_SAVED

Client = TestClient[web.Request, web.Application]
PAGES: Final[dict[str, str]] = {
    'device': '/device',
    'setup': '/setup',
    'device_power_poweroff': '/device/power?action=poweroff',
    'device_power_reboot': '/device/power?action=reboot',
    'settings': '/settings',
    'pins': '/pins',
}
REFUSALS: Final[dict[str, tuple[str, dict[str, str]]]] = {
    'setup': (
        '/setup',
        {'network': 'Home', 'password': 'pw12345678', 'api_key': 'short'},
    ),
    'device_key': ('/device/key', {'api_key': 'short'}),
}
NO_KEY_HOTSPOT: Final[DeviceStatus] = replace(HOTSPOT, key_set=False)


def display(*, status: DeviceStatus, keys: KeyStore) -> WebDisplay:
    """
    Wire a display with every page, its clocks pinned.

    Parameters
    ----------
    status : DeviceStatus
        What the device pages read.
    keys : KeyStore
        The API key store.

    Returns
    -------
    WebDisplay
        A display with device, pin, way and suggestion pages.
    """
    device = DevicePages(
        status=lambda: status,
        feeds=lambda: {TRAINS: NOW - 12, BUSES: NOW - 40},
        panel=lambda: None,
        keys=(DeviceKey(name='tfnsw', credential=TFNSW_KEY, store=keys),),
        feed_label=feed_label,
        daily_limit=DAILY_LIMIT,
        post=lambda event: None,
        zone=SYDNEY_ZONE,
        error_log=None,
        state_file=None,
        clock=lambda: NOW,
        nav=SYDNEY_NAV,
    )
    return WebDisplay(
        status=lambda: {'settings': form_values(settings=RICH_SAVED.settings)},
        device=device,
        nav=SYDNEY_NAV,
        hint=SYDNEY_HINT,
        board=PinPages(
            pins=Pins(pins=(WORK, HOME, ODD)).get,
            options=PLATFORMS,
            modes=SYDNEY.modes,
            plug=SuggestPlug(
                planner=FakeTripPlanner(), now=lambda: SEARCH_NOW,
            ),
        ),
        city=SYDNEY,
    )


async def answered(
    *, served: WebDisplay, path: str, form: dict[str, str] | None = None,
) -> str:
    """
    Fetch a page, or post a form, without following redirects.

    Parameters
    ----------
    served : WebDisplay
        The display serving the page.
    path : str
        Path and query.
    form : dict[str, str] | None
        Form fields to post; None to get the page.

    Returns
    -------
    str
        The status line, then the body.
    """
    client: Client = TestClient(TestServer(served.app()))
    await client.start_server()
    try:
        if form is None:
            response = await client.get(path, allow_redirects=False)
        else:
            response = await client.post(
                path, data=form, allow_redirects=False,
            )
        return f'{response.status} {response.reason}\n{await response.text()}'
    finally:
        await client.close()


def check_text(*, path: Path, text: str) -> None:
    """
    Check text against its baseline file.

    Parameters
    ----------
    path : Path
        The baseline file.
    text : str
        What the code gives today.
    """
    stored = baseline_bytes(path=path, data=text.encode()).decode()
    assert text == stored


@pytest.mark.parametrize('name', list(PAGES))
async def test_page_matches_baseline(name: str, tmp_path: Path) -> None:
    """The page's markup is today's."""
    key_set = name != 'setup'
    served = display(
        status=STATUS if key_set else NO_KEY_HOTSPOT,
        keys=FakeKeys(key=KEY) if key_set else SecretsFile(
            path=tmp_path / 'key',
        ),
    )
    text = await answered(served=served, path=PAGES[name])
    status_line, _, page = text.partition('\n')
    assert status_line == '200 OK'
    check_text(path=GOLDEN / 'pages' / f'{name}.html', text=page)


@pytest.mark.parametrize('name', list(REFUSALS))
async def test_refusal_matches_baseline(name: str, tmp_path: Path) -> None:
    """A short API key is refused with today's status and copy."""
    path, form = REFUSALS[name]
    served = display(
        status=NO_KEY_HOTSPOT if name == 'setup' else STATUS,
        keys=SecretsFile(path=tmp_path / 'key'),
    )
    text = await answered(served=served, path=path, form=form)
    check_text(path=GOLDEN / 'responses' / f'{name}.txt', text=text)


def test_routes_match_baseline() -> None:
    """Every route, in registration order, is today's."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    lines = [
        f'{route.method} {route.resource.canonical}'
        for route in app.router.routes()
        if route.resource is not None
    ]
    check_text(path=GOLDEN / 'routes.txt', text='\n'.join(lines) + '\n')


def test_only_vendor_route_is_dynamic() -> None:
    """Every route but `/vendor/{name}` has a plain path."""
    app = display(status=STATUS, keys=FakeKeys(key=KEY)).app()
    dynamic = [
        resource.canonical
        for resource in app.router.resources()
        if not isinstance(resource, web.PlainResource)
    ]
    assert dynamic == ['/vendor/{name}']
