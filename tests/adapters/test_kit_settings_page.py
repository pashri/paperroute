"""The kit's Settings page serves Sydney's page byte for byte."""

from collections.abc import Mapping
from typing import Final

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.device_form import DEVICE_SWITCHES
from paperroute_device.adapters.settings_page import SettingsPage
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_sydney.adapters.web_board import SYDNEY_NAV
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.board_web import board_text
from paperroute_transit.adapters.settings_form import (
    RADIOS,
    SWITCHES,
    form_values,
)
from paperroute_transit.core.saved import default_settings
from tests.golden.scenes import GOLDEN
from tests.golden.test_golden_state import RICH_SAVED

Client = TestClient[web.Request, web.Application]
FIRST: Final[str] = (
    '    <section class="card setting" aria-labelledby="display">'
)
LAST: Final[str] = '  </main>'
DEVICE_KEYS: Final[frozenset[str]] = frozenset({
    *DEVICE_SWITCHES, 'quiet_mode', 'clean_after', 'quiet_start', 'quiet_end',
})


class SydneySections:
    """Sydney's Display, Alerts and Timetable sections, cut from its page."""

    switches: Final[frozenset[str]] = frozenset(SWITCHES) - DEVICE_KEYS
    radios: Final[frozenset[str]] = frozenset(RADIOS) - DEVICE_KEYS
    sheets: Final[tuple[str, ...]] = ('board.css',)
    errors: Final[Mapping[str, str]] = {}

    def sections(self) -> str:
        """
        Cut the sections after Device from transit's page.

        Returns
        -------
        str
            The sections' lines.
        """
        page = board_text(name='settings.html')
        return page[page.index(FIRST):page.index(LAST)]

    def values(self, *, status: Mapping[str, object]) -> dict[str, str]:
        """
        Read the board's values as reported.

        Parameters
        ----------
        status : Mapping[str, object]
            The status fields.

        Returns
        -------
        dict[str, str]
            The board fields over the city's defaults.
        """
        values = form_values(settings=default_settings(city=SYDNEY))
        reported = status.get('settings')
        if isinstance(reported, Mapping):
            values.update(reported)
        return {
            key: value
            for key, value in values.items()
            if key not in DEVICE_KEYS
        }

    def change_from(self, *, fields: Mapping[str, str]) -> object | None:
        """
        Read nothing.

        Parameters
        ----------
        fields : Mapping[str, str]
            Ignored.

        Returns
        -------
        object | None
            Always None.
        """
        del fields
        return None

    def posted_values(
        self, *, fields: Mapping[str, str], change: object,
    ) -> dict[str, str]:
        """
        Write nothing.

        Parameters
        ----------
        fields : Mapping[str, str]
            Ignored.
        change : object
            Ignored.

        Returns
        -------
        dict[str, str]
            Nothing.
        """
        del fields, change
        return {}


async def test_sydney_page_is_the_golden() -> None:
    """The kit page with Sydney's sections is the golden page exactly."""
    status = {'settings': form_values(settings=RICH_SAVED.settings)}
    page = SettingsPage(
        status=lambda: status,
        post=lambda event: None,
        plug=SydneySections(),
        quiet_label='Follow the timetable',
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
