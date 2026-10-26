"""Tests for the connection editor page."""

import re
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Final

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.connection_editor import ALLOW_RANGE
from paperroute.adapters.pin_windows import NO_DAY
from paperroute.adapters.web_display import WebDisplay
from paperroute.core.messages import PinSaved
from paperroute.core.model import pin_ref
from paperroute.observers import PinsSeen
from tests.adapters.test_connection_editor import OPTIONS, WORK_FOUR
from tests.adapters.test_web_device import Device
from tests.adapters.test_web_pins import Pins
from tests.core.connection_factories import WORK

Client = TestClient[web.Request, web.Application]

FULL: Final[list[tuple[str, str]]] = [
    ('stop', 'G2145615'), ('kind', 'bus'), ('route', '606'),
    ('off', 'G215254'), ('on', '2145648'), ('allow', '4'),
    ('allow_for', '2145648'), ('to', '214510'), ('nickname', 'Work'),
    ('wn', '0'),
]


@pytest.fixture(name='pins')
def _pins() -> Pins:
    """The Work connection, on screen."""
    pins = Pins(pins=())
    pins.seen = PinsSeen(pins=(WORK,), shown=0, revision=3)
    return pins


@pytest.fixture(name='client')
async def _client(pins: Pins) -> AsyncIterator[Client]:
    """A client of a display with the fake timetable."""
    display = WebDisplay(
        device=Device().pages(), pins=pins.get, options=OPTIONS,
    )
    display.bind(post=pins.posted.append)
    display.pins_sleep = pins.sleep
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    yield client
    await client.close()


def unlabelled(*, text: str) -> list[str]:
    """
    List the visible controls with no label.

    Parameters
    ----------
    text : str
        The page.

    Returns
    -------
    list[str]
        IDs, or tags, of selects and inputs that no label names or holds.
    """
    labels = set(re.findall(r'<label for="([^"]+)"', text))
    found = []
    for tag in re.findall(r'<(?:select|input)\b[^>]*>', text):
        if 'type="hidden"' in tag:
            continue
        ident = re.search(r'id="([^"]+)"', tag)
        held = f'<label>{tag}' in text
        if (ident is None or ident[1] not in labels) and not held:
            found.append(tag)
    return found


async def test_page_without_js(client: Client) -> None:
    """Each step is a GET form showing the next choices, all labelled."""
    response = await client.get(
        '/pins/connect?stop=G2145615&route=606&off=G215254',
    )
    text = await response.text()
    assert response.status == HTTPStatus.OK
    assert '<form class="editor" method="get" action="/pins/connect">' in (
        text
    )
    assert '<label for="pin-on">Get on at</label>' in text
    assert 'Constitution Hill, North West Twy · 150 m' in text
    assert 'Stops within 500 m of where you get off, nearest first.' in text
    assert '<legend>Leg 1</legend>' in text
    assert '<legend>Change</legend>' in text
    assert 'Save connection' not in text
    assert '<title>Add a connection - paperroute</title>' in text
    assert unlabelled(text=text) == []
    assert '<script>' not in text


async def test_later_steps_offered(client: Client) -> None:
    """Choosing where to get on offers the allowance, the To and routes."""
    query = '&'.join(f'{name}={value}' for name, value in FULL[:7])
    text = await (await client.get(f'/pins/connect?{query}')).text()
    assert 'Worked out from the 150 m walk.' in text
    assert '<label for="pin-allow">Allow (minutes)</label>' in text
    assert '<legend>Leg 2</legend>' in text
    assert '<label for="pin-to">To</label>' in text
    assert unlabelled(text=text) == []
    full = '&'.join(f'{name}={value}' for name, value in FULL)
    text = await (await client.get(f'/pins/connect?{full}')).text()
    assert 'Any route that gets there: 660, 665.' in text
    assert 'name="only2" value="665"' in text
    assert 'formaction="/pins/connect/save"' in text
    assert unlabelled(text=text) == []


async def test_save_posts_connection(pins: Pins, client: Client) -> None:
    """A full save posts the connection and returns to the list."""
    pins.seen = PinsSeen(pins=(), shown=None, revision=3)
    response = await client.post(
        '/pins/connect/save', data=FULL, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?seen=3&done=saved'
    assert pins.posted == [PinSaved(ref=None, pin=WORK_FOUR)]


async def test_save_refuses_allow(pins: Pins, client: Client) -> None:
    """An allowance of 0 is refused with the fields kept."""
    data = [(name, '0' if name == 'allow' else value) for name, value in FULL]
    response = await client.post('/pins/connect/save', data=data)
    text = await response.text()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert ALLOW_RANGE in text
    assert 'name="allow"' in text
    assert 'value="0"' in text
    assert '<option value="214510" selected>' in text
    assert pins.posted == []


async def test_save_refuses_misfit(pins: Pins, client: Client) -> None:
    """A To that is not offered is refused."""
    data = [
        (name, 'nowhere' if name == 'to' else value) for name, value in FULL
    ]
    response = await client.post('/pins/connect/save', data=data)
    text = await response.text()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert "Those choices don't fit together. Pick again." in text
    assert pins.posted == []


async def test_save_of_duplicate_says_so(client: Client) -> None:
    """Saving the Work connection again says it exists."""
    data = [
        (name, '7' if name == 'allow' else value) for name, value in FULL
    ]
    response = await client.post(
        '/pins/connect/save', data=data, allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins?done=duplicate'


async def test_edit_existing_connection(client: Client) -> None:
    """Editing a connection fills every field and offers Delete."""
    ref = pin_ref(pin=WORK)
    text = await (await client.get(f'/pins/connect?ref={ref}')).text()
    assert '<title>Edit connection - paperroute</title>' in text
    assert f'name="ref" value="{ref}"' in text
    assert '<option value="G215254" selected>' in text
    assert '<option value="2145648" selected>' in text
    assert 'name="allow" value="7"' in text
    assert '<option value="214510" selected>' in text
    assert 'value="Work"' in text
    assert '>Delete</button>' in text


async def test_edit_of_gone_ref(client: Client) -> None:
    """An unknown ref goes back to the list."""
    response = await client.get(
        '/pins/connect?ref=nothing', allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?done=gone'


async def test_resave_existing(pins: Pins, client: Client) -> None:
    """Saving an edited connection posts it under its ref."""
    ref = pin_ref(pin=WORK)
    data = [*FULL, ('ref', ref)]
    response = await client.post(
        '/pins/connect/save', data=data, allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins?seen=3&done=saved'
    assert pins.posted == [PinSaved(ref=ref, pin=WORK_FOUR)]


async def test_starting_without_timetable(pins: Pins) -> None:
    """Before the timetable is ready the page says so."""
    display = WebDisplay(device=Device().pages(), pins=pins.get, options=None)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    text = await (await client.get('/pins/connect?stop=G2145615')).text()
    await client.close()
    assert 'Starting up. Reload in a moment.' in text


async def test_save_refuses_window(pins: Pins, client: Client) -> None:
    """A window with no day is refused like on the pin editor."""
    data = [
        *(item for item in FULL if item[0] != 'wn'),
        ('wn', '1'), ('w0_from', '07:00'), ('w0_to', '08:00'),
    ]
    response = await client.post('/pins/connect/save', data=data)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert NO_DAY in await response.text()
    assert pins.posted == []


async def test_first_step_has_no_change(client: Client) -> None:
    """Before the stop to get off at, only leg 1 is shown."""
    text = await (await client.get('/pins/connect?stop=G2145615')).text()
    assert '<label for="pin-off">Get off at</label>' in text
    assert '<legend>Change</legend>' not in text
    assert '<legend>Leg 2</legend>' not in text


async def test_save_before_timetable(pins: Pins) -> None:
    """A save before the timetable is ready goes back to the list."""
    display = WebDisplay(device=Device().pages(), pins=pins.get, options=None)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    response = await client.post(
        '/pins/connect/save', data=FULL, allow_redirects=False,
    )
    await client.close()
    assert response.headers['Location'] == '/pins'
    assert pins.posted == []
