"""The kit's Settings page serves Sydney's page byte for byte."""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.settings_page import SettingsPage
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_sydney.adapters.web_board import SYDNEY_NAV
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.board_settings import BoardSettingsPlug
from paperroute_transit.adapters.settings_form import form_values
from paperroute_transit.core.saved import default_settings
from paperroute_transit.transit_app import QUIET_LABEL
from tests.golden.scenes import GOLDEN
from tests.golden.test_golden_state import RICH_SAVED

Client = TestClient[web.Request, web.Application]


async def test_sydney_page_is_the_golden() -> None:
    """The kit page with Sydney's sections is the golden page exactly."""
    status = {'settings': form_values(settings=RICH_SAVED.settings)}
    page = SettingsPage(
        status=lambda: status,
        post=lambda event: None,
        plug=BoardSettingsPlug(defaults=default_settings(city=SYDNEY)),
        quiet_label=QUIET_LABEL,
        nav=SYDNEY_NAV,
        sheets=('palette.css',),
    )
    client: Client = TestClient(TestServer(WebDisplay(settings=page).app()))
    await client.start_server()
    try:
        text = await (await client.get('/settings')).text()
    finally:
        await client.close()
    golden = (GOLDEN / 'pages' / 'settings.html').read_text(encoding='utf-8')
    assert text == golden
