"""Tests for the kit's Settings page."""

import re
from collections.abc import AsyncIterator
from datetime import time
from http import HTTPStatus

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.device_form import ERRORS
from paperroute_device.adapters.settings_page import (
    QUIET_APP_RADIO,
    SettingsPage,
    line_filled,
)
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.core.device_settings import QuietMode, Window
from paperroute_device.core.kit_events import DeviceChange, SettingsPosted
from tests.adapters.settings_plug import ALARM_ERROR, ClockChange, ClockPlug

Client = TestClient[web.Request, web.Application]
LABEL = 'Follow the clock'
DEVICE_FORM = [
    ('rotate', 'off'),
    ('rotate', 'on'),
    ('larger_text', 'off'),
    ('quiet_mode', 'app'),
    ('quiet_start', '23:00'),
    ('quiet_end', '05:30'),
]


class Site:
    """
    A Settings page over a status the test changes, recording posts.

    Parameters
    ----------
    plug : ClockPlug | None
        The app's sections, or None.
    quiet_label : str | None
        The app's quiet label, or None.

    Attributes
    ----------
    status : dict[str, object]
        What the status reports.
    posted : list[object]
        The events posted.
    page : SettingsPage
        The page.
    """

    def __init__(
        self, *, plug: ClockPlug | None, quiet_label: str | None,
    ) -> None:
        self.status: dict[str, object] = {}
        self.posted: list[object] = []
        self.page = SettingsPage(
            status=lambda: self.status,
            post=self.posted.append,
            plug=plug,
            quiet_label=quiet_label,
        )

    async def client(self) -> Client:
        """
        Serve the page behind the kit's web display.

        Returns
        -------
        Client
            A started client.
        """
        client = TestClient(TestServer(WebDisplay(settings=self.page).app()))
        await client.start_server()
        return client


@pytest.fixture(name='site')
def _site() -> Site:
    """A page with the clock's section and a quiet label."""
    return Site(plug=ClockPlug(), quiet_label=LABEL)


@pytest.fixture(name='client')
async def _client(site: Site) -> AsyncIterator[Client]:
    """A client of the site."""
    client = await site.client()
    yield client
    await client.close()


async def page_of(*, plug: ClockPlug | None, label: str | None) -> str:
    """
    Fetch the Settings page.

    Parameters
    ----------
    plug : ClockPlug | None
        The app's sections, or None.
    label : str | None
        The quiet label, or None.

    Returns
    -------
    str
        The page.
    """
    client = await Site(plug=plug, quiet_label=label).client()
    try:
        return await (await client.get('/settings')).text()
    finally:
        await client.close()


async def test_device_fields_post_one_event(
    site: Site, client: Client,
) -> None:
    """A Device form post posts one event, with no app part, and redirects."""
    response = await client.post(
        '/settings', data=DEVICE_FORM, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert site.posted == [SettingsPosted(
        device=DeviceChange(
            rotate=True,
            larger_text=False,
            quiet_mode=QuietMode.APP,
            my_hours=Window(start=time(23), end=time(5, 30)),
        ),
    )]


async def test_app_fields_post_in_the_same_event(
    site: Site, client: Client,
) -> None:
    """Device and app fields in one post make one event; JSON gets 204."""
    response = await client.post(
        '/settings', json={'rotate': 'on', 'chime': 'on', 'alarm': '06:15'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    assert site.posted == [SettingsPosted(
        device=DeviceChange(rotate=True),
        app=ClockChange(chime=True, alarm=time(6, 15)),
    )]


@pytest.mark.parametrize('fields', [
    {'rotate': 'maybe'},
    {'clean_after': '7'},
    {'quiet_mode': 'timetable'},
    {'colour': 'green'},
    {'chime': 'loud', 'rotate': 'on'},
])
async def test_unknown_value_is_400(
    site: Site, client: Client, fields: dict[str, str],
) -> None:
    """A device or app value the page never sends is refused."""
    response = await client.post('/settings', data=fields)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not site.posted


@pytest.mark.parametrize(('fields', 'section', 'message'), [
    (
        {'quiet_mode': 'my_hours', 'quiet_start': '25:00',
         'quiet_end': '07:00'},
        'quiet',
        ERRORS['quiet'],
    ),
    ({'alarm': '7am'}, 'alarm', ALARM_ERROR),
])
async def test_bad_time_is_422_for_json_and_303_for_forms(
    site: Site,
    client: Client,
    fields: dict[str, str],
    section: str,
    message: str,
) -> None:
    """An unusable time is 422 with its section's text, or the error line."""
    as_json = await client.post('/settings', json=fields)
    as_form = await client.post(
        '/settings', data=fields, allow_redirects=False,
    )
    shown = await (await client.get(f'/settings?error={section}')).text()
    plain = await (await client.get('/settings')).text()
    assert as_json.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert await as_json.json() == {'error': message}
    assert as_form.status == HTTPStatus.SEE_OTHER
    assert as_form.headers['Location'] == f'/settings?error={section}'
    assert f'<p class="error" role="alert">{message}</p>' in shown
    assert 'not saved' not in plain
    assert not site.posted


async def test_page_without_plug_has_only_the_device_section() -> None:
    """With no plug the page holds the Device section and no mark."""
    text = await page_of(plug=None, label=LABEL)
    assert re.findall(r'<h2 id="([a-z]+)">', text) == ['device']
    assert '{{' not in text
    assert '  <link rel="stylesheet" href="/live.css">\n  <script' in text
    assert '    </section>\n  </main>' in text


async def test_page_with_plug_puts_its_sections_after_device() -> None:
    """The app's sections follow Device, filled with the app's values."""
    text = await page_of(plug=ClockPlug(), label=LABEL)
    assert (
        '  <link rel="stylesheet" href="/live.css">\n'
        '  <link rel="stylesheet" href="/clock.css">\n'
        '  <script'
    ) in text
    assert re.findall(r'<h2 id="([a-z]+)">', text) == ['device', 'clock']
    assert 'name="colour" value="red" checked>' in text
    assert 'name="alarm" value="07:00">' in text
    assert '{{' not in text


async def test_page_without_quiet_label_offers_only_my_hours() -> None:
    """With no quiet label there is no app radio, and posting it is 400."""
    site = Site(plug=None, quiet_label=None)
    client = await site.client()
    try:
        text = await (await client.get('/settings')).text()
        refused = await client.post('/settings', data={'quiet_mode': 'app'})
    finally:
        await client.close()
    assert re.findall(r'name="quiet_mode" value="([a-z_]+)"', text) == [
        'my_hours',
    ]
    assert '\n\n' not in text.split('<fieldset>')[1].split('</fieldset>')[0]
    assert refused.status == HTTPStatus.BAD_REQUEST
    assert not site.posted


async def test_quiet_label_is_text() -> None:
    """The label is escaped, and a mark in it is never filled."""
    text = await page_of(plug=None, label='Sun <up> & {{rotate}}')
    assert re.findall(r'name="quiet_mode" value="([a-z_]+)"', text) == [
        'my_hours', 'app',
    ]
    assert '            Sun &lt;up&gt; &amp; &#123;&#123;rotate}}\n' in text


async def test_page_shows_posted_before_status(
    site: Site, client: Client,
) -> None:
    """The posted values win until the status reports them."""
    site.status = {'settings': {'rotate': 'off', 'chime': 'off'}}
    rotate = re.compile(r'name="rotate"\s+value="on" checked>')
    chime = re.compile(r'name="chime"\s+value="on" checked>')
    await client.post('/settings', data={'rotate': 'on', 'chime': 'on'})
    shown = await (await client.get('/settings')).text()
    site.status = {'settings': {'rotate': 'on', 'chime': 'on'}}
    later = await (await client.get('/settings')).text()
    site.status = {'settings': {'rotate': 'off', 'chime': 'off'}}
    after = await (await client.get('/settings')).text()
    assert rotate.search(shown) and chime.search(shown)
    assert rotate.search(later) and chime.search(later)
    assert not rotate.search(after) and not chime.search(after)


async def test_later_change_elsewhere_shows_on_page(
    site: Site, client: Client,
) -> None:
    """A change at the panel after a save is not hidden by the overlay."""
    site.status = {'settings': {'rotate': 'off'}}
    await client.post('/settings', data={'rotate': 'on'})
    site.status = {'settings': {'rotate': 'off', 'larger_text': 'on'}}
    text = await (await client.get('/settings')).text()
    assert re.search(r'name="rotate"\s+value="on">', text)
    assert re.search(r'name="larger_text"\s+value="on" checked>', text)


async def test_script_is_served(client: Client) -> None:
    """The page's script is served as JavaScript."""
    script = await client.get('/settings.js')
    assert script.status == HTTPStatus.OK
    assert script.content_type in {'text/javascript', 'application/javascript'}


async def test_cross_site_post_is_refused(
    site: Site, client: Client,
) -> None:
    """A post from another site is refused."""
    response = await client.post(
        '/settings',
        data={'rotate': 'on'},
        headers={'Origin': 'http://evil.example'},
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not site.posted


def test_quiet_app_radio_holds_its_mark() -> None:
    """The radio keeps its own choice mark for the page to fill."""
    assert '{{quiet_mode:app}}' in QUIET_APP_RADIO


@pytest.mark.parametrize(('lines', 'expected'), [
    ('', 'a\nc\n'),
    ('  x\n  y\n', 'a\n  x\n  y\nc\n'),
    ('  x', 'a\n  x\nc\n'),
])
def test_line_filled_replaces_the_marks_whole_line(
    lines: str, expected: str,
) -> None:
    """The mark's line goes; empty lines leave no blank line."""
    assert line_filled(page='a\n    {{m}}\nc\n', mark='{{m}}', lines=lines) == (
        expected
    )

