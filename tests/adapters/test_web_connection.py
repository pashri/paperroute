"""Tests for the hand way editor page."""

import re
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.connection_editor import ALLOW_RANGE
from paperroute.adapters.web_board import SydneyPages
from paperroute.adapters.web_display import WebDisplay
from paperroute.adapters.web_pin_edit import MISFIT
from paperroute.core.model import pin_ref
from paperroute.observers import PinsSeen
from paperroute_testkit.core.connection_factories import WORK
from tests.adapters.test_connection_editor import OPTIONS
from tests.adapters.test_web_device import Device
from tests.adapters.test_web_pins import Pins

Client = TestClient[web.Request, web.Application]

FULL: Final[list[tuple[str, str]]] = [
    ('stop', 'G2145615'), ('off', 'G215254'), ('on', '2145648'), ('allow', '4'),
    ('allow_for', '2145648'), ('to', '214510'),
]
TWO: Final[list[tuple[str, str]]] = [
    *FULL[:5], ('changes', '2'), ('off2', '214510'), ('on2', '2145648'),
    ('allow2', '3'), ('allow2_for', '2145648'), ('to', '214510'),
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
    board = SydneyPages(pins=pins.get, options=OPTIONS, planner=None)
    board.pins_sleep = pins.sleep
    display = WebDisplay(device=Device().pages(), board=board)
    display.bind(post=pins.posted.append)
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
        held = f'<label>{tag}' in text or re.search(
            r'<label[^>]*>' + re.escape(tag), text,
        )
        if (ident is None or ident[1] not in labels) and not held:
            found.append(tag)
    return found


def query(fields: list[tuple[str, str]]) -> str:
    """
    Write fields as a query string.

    Parameters
    ----------
    fields : list[tuple[str, str]]
        Fields.

    Returns
    -------
    str
        The query.
    """
    return urlencode(fields)


async def test_works_without_js(client: Client) -> None:
    """Every step is reachable by a plain GET, all controls labelled."""
    start = query([FULL[0], FULL[-1]])
    text = await (await client.get(f'/pins/connect?{start}')).text()
    assert '<label for="pin-off">Get off at</label>' in text
    assert 'value="G215254">Hammers Rd Opp Sherwood St</option>' in text
    assert 'name="kind"' not in text and 'name="route"' not in text
    assert '<legend>Change</legend>' not in text
    text = await (
        await client.get(f'/pins/connect?{query([*FULL[:2], FULL[-1]])}')
    ).text()
    assert 'Constitution Hill, North West Twy · 150 m' in text
    text = await (await client.get(f'/pins/connect?{query(FULL)}')).text()
    assert 'Worked out from the 150 m walk.' in text
    assert 'Then on to Westmead Station.' in text
    assert 'name="more" value="1"' in text
    assert 'formaction="/pins/connect/save"' in text
    more = await (
        await client.get(f'/pins/connect?{query([*FULL, ("more", "1")])}')
    ).text()
    assert 'name="changes" value="2"' in more
    assert '<label for="pin-off2">Get off at</label>' in more
    assert 'name="more" value="1"' not in more
    text = await (await client.get(f'/pins/connect?{query(TWO)}')).text()
    assert '<legend>Change 2</legend>' in text
    assert '<label for="pin-allow2">Allow (minutes)</label>' in text
    assert '<legend>Leg 3</legend>' in text
    assert unlabelled(text=text) == []
    assert '<script>' not in text


async def test_show_choices_keeps_from_and_to(client: Client) -> None:
    """The form carries its From and To, so Show choices keeps them."""
    text = await (
        await client.get(f'/pins/connect?{query([FULL[0], FULL[-1]])}')
    ).text()
    assert '<input type="hidden" name="stop" value="G2145615">' in text
    assert '<input type="hidden" name="to" value="214510">' in text
    assert 'Hollis St Opp Constitution Hill Library → Westmead Station' in text


async def test_unreached_to_says_so(client: Client) -> None:
    """A last ride that does not reach the To says so and cannot be added."""
    fields = [*FULL[:-1], ('to', 'G215254')]
    text = await (await client.get(f'/pins/connect?{query(fields)}')).text()
    assert 'No ride from Constitution Hill, North West Twy reaches' in text
    assert 'formaction="/pins/connect/save"' not in text


async def test_part_steps_returns_fieldset(client: Client) -> None:
    """`part=steps` answers with only the steps fieldset."""
    response = await client.get(
        f'/pins/connect?{query([*FULL, ("part", "steps")])}',
    )
    text = await response.text()
    assert response.status == HTTPStatus.OK
    assert text.strip().startswith('<fieldset class="steps">')
    assert text.strip().endswith('</fieldset>')
    assert '<html' not in text


async def test_third_change_refused(client: Client) -> None:
    """Asking for a third change is refused."""
    response = await client.get(
        f'/pins/connect?{query([*TWO, ("more", "1")])}',
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert MISFIT in await response.text()


async def test_hand_way_two_changes(pins: Pins, client: Client) -> None:
    """Adding a two-change way goes back to the ways page with it."""
    back = urlencode([('ref', pin_ref(pin=WORK)), ('from', 'G2145615'),
                      ('to', '214510'), ('ways', 'G215254,2145648,7'),
                      ('way', '0'), ('never', '660')])
    response = await client.post(
        '/pins/connect/save', data=[*TWO, ('back', back)],
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    location = urlsplit(response.headers['Location'])
    assert location.path == '/pins/ways'
    fields = parse_qsl(location.query)
    assert ('ways', 'G215254,2145648,7') in fields
    assert ('ways', 'G215254,2145648,4|214510,2145648,3') in fields
    assert ('way', '0') in fields and ('way', '1') in fields
    assert ('never', '660') in fields
    assert ('ref', pin_ref(pin=WORK)) in fields
    assert pins.posted == []


async def test_hand_way_keeps_back_fields(client: Client) -> None:
    """Fields the ways page had, windows among them, come back with it."""
    back = urlencode([('from', 'G2145615'), ('to', '214510'),
                      ('nickname', 'Office'), ('wn', '1'),
                      ('w0_day', 'mon'), ('w0_from', '07:00'),
                      ('w0_to', '09:00')])
    response = await client.post(
        '/pins/connect/save', data=[*FULL, ('back', back)],
        allow_redirects=False,
    )
    location = urlsplit(response.headers['Location'])
    assert parse_qsl(location.query) == [
        ('from', 'G2145615'), ('to', '214510'), ('nickname', 'Office'),
        ('wn', '1'), ('w0_day', 'mon'), ('w0_from', '07:00'),
        ('w0_to', '09:00'), ('ways', 'G215254,2145648,4'), ('way', '0'),
    ]


async def test_new_way_starts_ways_page(client: Client) -> None:
    """Without a ways page to go back to, it starts one for a new pin."""
    response = await client.post(
        '/pins/connect/save', data=FULL, allow_redirects=False,
    )
    location = urlsplit(response.headers['Location'])
    assert parse_qsl(location.query) == [
        ('from', 'G2145615'), ('to', '214510'),
        ('ways', 'G215254,2145648,4'), ('way', '0'),
    ]


async def test_save_refuses_allow(client: Client) -> None:
    """An allowance of 0 is refused with the fields kept."""
    data = [(name, '0' if name == 'allow' else value) for name, value in FULL]
    response = await client.post('/pins/connect/save', data=data)
    text = await response.text()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert ALLOW_RANGE in text
    assert 'name="allow" value="0"' in text
    assert '<input type="hidden" name="to" value="214510">' in text


async def test_save_refuses_misfit(client: Client) -> None:
    """A To that is not offered, or not the ways page's To, is refused."""
    data = [
        (name, 'G215254' if name == 'to' else value) for name, value in FULL
    ]
    response = await client.post('/pins/connect/save', data=data)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert MISFIT in await response.text()
    other = urlencode([('from', 'G2145615'), ('to', '2150')])
    response = await client.post(
        '/pins/connect/save', data=[*FULL, ('back', other)],
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    response = await client.post(
        '/pins/connect/save', data=[*TWO, ('more', '1')],
    )
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_starting_without_timetable(pins: Pins) -> None:
    """Before the timetable is ready the page says so; saves go back."""
    display = WebDisplay(
        device=Device().pages(),
        board=SydneyPages(pins=pins.get, options=None, planner=None),
    )
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    text = await (await client.get('/pins/connect?stop=G2145615')).text()
    response = await client.post(
        '/pins/connect/save', data=FULL, allow_redirects=False,
    )
    await client.close()
    assert 'Starting up. Reload in a moment.' in text
    assert response.headers['Location'] == '/pins'


async def test_editor_without_stops_goes_to_ways_page(
    client: Client,
) -> None:
    """Without a known From and To, it goes back to the ways page."""
    response = await client.get('/pins/connect', allow_redirects=False)
    assert response.headers['Location'] == '/pins/ways'
    back = urlencode([('from', 'G2145615'), ('nickname', 'Office')])
    response = await client.get(
        f'/pins/connect?{query([FULL[0], ("back", back)])}',
        allow_redirects=False,
    )
    assert response.headers['Location'] == f'/pins/ways?{back}'
