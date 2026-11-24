"""Tests for web display: settings."""

import re
from datetime import time as clock
from http import HTTPStatus

import pytest

from paperroute_device.adapters.device_form import ERRORS
from paperroute_device.adapters.web_frame import LIVE_NAV
from paperroute_device.adapters.web_live import live_page
from paperroute_device.core.device_settings import QuietMode, Window
from paperroute_sydney.adapters.web_board import SYDNEY_HINT, SYDNEY_NAV
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.kit_steps import settings_posted
from paperroute_testkit.page_markup import parse_page
from paperroute_transit.adapters.board_head import SETTINGS_NAV
from paperroute_transit.core.saved import Rate
from tests.adapters.transit_web import transit_display
from tests.adapters.web_display.helpers import Client, _client, page_text


async def test_settings_post_posts_event(
    posted: list[object], client: Client
) -> None:
    """A switch change posts an event; bad values and other sites fail."""
    good = await client.post('/settings', json={'auto_rebuild': 'off'})
    maybe = await client.post('/settings', json={'auto_rebuild': 'maybe'})
    cross = await client.post(
        '/settings',
        json={'auto_rebuild': 'on'},
        headers={'Origin': 'http://evil.example'},
    )
    assert good.status == HTTPStatus.NO_CONTENT
    assert maybe.status == HTTPStatus.BAD_REQUEST
    assert cross.status == HTTPStatus.FORBIDDEN
    assert posted == [settings_posted(auto_rebuild=False)]


@pytest.mark.parametrize(('status', 'checked'), [
    ({}, True),
    ({'settings': {'auto_rebuild': 'on'}}, True),
    ({'settings': {'auto_rebuild': 'off'}}, False),
])
async def test_settings_page_served(
    status: dict[str, object], checked: bool
) -> None:
    """The page has the switch, drawn as reported, and a link to Live."""
    display = transit_display(status=lambda: status, city=SYDNEY)
    client = await _client(display=display)
    try:
        page = await client.get('/settings')
        text = await page.text()
        script = await client.get('/settings.js')
    finally:
        await client.close()
    assert page.content_type == 'text/html'
    assert 'Rebuild timetable automatically' in text
    assert ('/', False, 'Live') in parse_page(text=text).links
    assert ('aria-describedby="auto-rebuild-hint" checked>' in text) is (
        checked
    )
    assert '{{' not in text
    assert script.content_type in {
        'text/javascript', 'application/javascript',
    }


async def test_settings_page_links_sheets_once() -> None:
    """The settings page links the city's sheet once, between the others."""
    display = transit_display(
        status=dict,
        city=SYDNEY,
        sheets=('palette.css',),
    )
    text = await page_text(display=display, path='/settings')
    assert text.count('href="/palette.css"') == 1
    assert (
        '  <link rel="stylesheet" href="/live.css">\n'
        '  <link rel="stylesheet" href="/palette.css">\n'
        '  <link rel="stylesheet" href="/board.css">\n'
    ) in text


async def test_settings_nav_comes_from_the_city() -> None:
    """The settings page lists the city's links, Settings current."""
    display = transit_display(
        status=dict, city=SYDNEY, nav=(LIVE_NAV, SETTINGS_NAV),
    )
    text = await page_text(display=display, path='/settings')
    found = re.search(r'<nav\b.*?</nav>', text, flags=re.DOTALL)
    assert found is not None
    assert re.findall(
        r'<a href="([^"]*)"( aria-current="page")?>', found.group(0),
    ) == [('/', ''), ('/settings', ' aria-current="page"')]


async def test_settings_page_has_connection_map() -> None:
    """The Display form has the connection map radios, Changes checked."""
    client = await _client(display=transit_display(status=dict, city=SYDNEY))
    try:
        text = await (await client.get('/settings')).text()
    finally:
        await client.close()
    assert re.search(
        r'<input type="radio" name="connection_map" value="changes" '
        r'checked aria-describedby="map-changes-hint">',
        text,
    )
    assert 'value="whole" aria-describedby' in text


def test_live_page_links_to_settings() -> None:
    """The Live page's header links to Settings."""
    html = live_page(nav=SYDNEY_NAV, hint=SYDNEY_HINT)
    assert ('/settings', False, 'Settings') in parse_page(text=html).links


DISPLAY_FORM = [
    ('peak_rate', '60s'),
    ('offpeak_rate', '2min'),
    ('peak_1_start', '07:00'),
    ('peak_1_end', '09:00'),
    ('peak_2_start', ''),
    ('peak_2_end', ''),
    ('skip_school_buses', 'off'),
    ('skip_school_buses', 'on'),
]


DEVICE_FORM = [
    ('rotate', 'off'),
    ('rotate', 'on'),
    ('larger_text', 'off'),
    ('inverted_lists', 'off'),
    ('quiet_mode', 'app'),
    ('quiet_start', '23:00'),
    ('quiet_end', '05:30'),
]


async def test_display_form_posts_event(
    posted: list[object], client: Client
) -> None:
    """A form post of the Display section posts one event and redirects."""
    response = await client.post(
        '/settings', data=DISPLAY_FORM, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert posted == [settings_posted(
        peak_rate=Rate.STEADY,
        offpeak_rate=Rate.SLOW,
        peak_windows=(Window(start=clock(7), end=clock(9)),),
        skip_school_buses=True,
    )]


async def test_device_form_posts_event(
    posted: list[object], client: Client
) -> None:
    """A form post of the Device section posts one event and redirects."""
    response = await client.post(
        '/settings', data=DEVICE_FORM, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings'
    assert posted == [settings_posted(
        rotate=True,
        larger_text=False,
        inverted_lists=False,
        quiet_mode=QuietMode.APP,
        my_hours=Window(start=clock(23), end=clock(5, 30)),
    )]


async def test_json_post_answers_no_content(
    posted: list[object], client: Client
) -> None:
    """A JSON post is answered 204."""
    response = await client.post('/settings', json={'rotate': 'on'})
    assert response.status == HTTPStatus.NO_CONTENT
    assert posted == [settings_posted(rotate=True)]


async def test_bad_peak_redirects_with_error(
    posted: list[object], client: Client
) -> None:
    """A window ending before it starts posts nothing and shows an error."""
    response = await client.post(
        '/settings',
        data={'peak_1_start': '10:00', 'peak_1_end': '09:00'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings?error=peak'
    assert not posted
    page = await client.get('/settings?error=peak')
    text = await page.text()
    assert 'Peak hours not saved: each start must be before' in text
    assert 'Quiet hours not saved' not in text
    plain = await (await client.get('/settings')).text()
    assert 'not saved' not in plain


async def test_json_bad_time_is_422_with_the_error_text(
    posted: list[object], client: Client
) -> None:
    """A JSON post with an unusable time gets the error text to show."""
    response = await client.post(
        '/settings',
        json={
            'quiet_mode': 'my_hours',
            'quiet_start': '25:00',
            'quiet_end': '07:00',
        },
    )
    assert response.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert await response.json() == {'error': ERRORS['quiet']}
    assert not posted


async def test_form_bad_time_still_redirects(
    posted: list[object], client: Client
) -> None:
    """A form post with an unusable time goes back to the error line."""
    response = await client.post(
        '/settings',
        data={
            'quiet_mode': 'my_hours',
            'quiet_start': '25:00',
            'quiet_end': '07:00',
        },
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/settings?error=quiet'
    assert not posted
