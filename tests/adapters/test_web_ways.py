"""Tests for the ways page."""

from collections.abc import AsyncIterator
from dataclasses import replace
from http import HTTPStatus
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.pin_windows import NO_DAY
from paperroute.adapters.web_display import WebDisplay
from paperroute.adapters.web_pin_edit import MISFIT
from paperroute.adapters.web_pin_list import pin_label
from paperroute.core.messages import PinSaved
from paperroute.core.model import pin_ref
from paperroute.observers import PinsSeen
from paperroute_testkit.core.connection_factories import CAMPBELLTOWN_WAY, WORK
from tests.adapters.test_connection_editor import OPTIONS
from tests.adapters.test_web_connection import unlabelled
from tests.adapters.test_web_device import Device
from tests.adapters.test_web_pins import Pins, check_danger_card

Client = TestClient[web.Request, web.Application]
REF = pin_ref(pin=WORK)
SAVE = [
    ('ref', REF), ('from', 'G2145615'), ('to', '214510'),
    ('ways', 'G215254,2145648,7'), ('ways', 'G215254,2560,6'),
    ('way', '1'), ('nickname', 'Work'), ('wn', '0'),
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


async def test_page_of_saved_pin(client: Client) -> None:
    """A saved pin shows From and To, its ways, never list and Delete."""
    text = await (await client.get(f'/pins/ways?ref={REF}')).text()
    assert '<legend>From and to</legend>' in text
    assert 'name="from" data-stop-search' in text
    assert '<option value="214510" selected>Westmead Station</option>' in text
    assert 'Suggest with Trip Planner' in text
    assert '<legend>Ways to go</legend>' in text
    assert 'name="way"\n               value="0" checked>' in text
    assert 'via Hammers' not in text
    assert 'via Constitution Hill, North West Twy · 7 min' in text
    assert 'formaction="/pins/ways/hand"' in text
    assert '>Add a way by hand</button>' in text
    assert '<legend>Never use</legend>' in text
    assert 'Routes ticked here are never used on any leg.' in text
    assert 'value="Work"' in text
    assert '>Delete pin</button>' in text
    assert unlabelled(text=text) == []
    assert '<script src="/vendor/tom-select.complete.min.js" defer>' in text


async def test_ways_list_ticks_and_unticks(
    pins: Pins, client: Client,
) -> None:
    """An unticked way is kept when the pin is saved."""
    response = await client.post(
        '/pins/ways/save', data=SAVE, allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?seen=3&done=saved'
    saved = replace(WORK, ways=(
        replace(WORK.ways[0], ticked=False), CAMPBELLTOWN_WAY,
    ))
    assert pins.posted == [PinSaved(ref=REF, pin=saved)]
    pins.seen = PinsSeen(pins=(saved,), shown=0, revision=4)
    text = await (await client.get(f'/pins/ways?ref={REF}')).text()
    assert 'name="way"\n               value="0">' in text
    assert 'name="way"\n               value="1" checked>' in text


async def test_never_saved_and_shown(pins: Pins, client: Client) -> None:
    """A never route is saved and shows ticked."""
    await client.post(
        '/pins/ways/save', data=[*SAVE, ('never', '665')],
        allow_redirects=False,
    )
    saved = pins.posted[0]
    assert isinstance(saved, PinSaved)
    assert saved.pin.never == ('665',)
    query = urlencode([*SAVE, ('never', '665')])
    text = await (await client.get(f'/pins/ways?{query}')).text()
    assert 'name="never"\n               value="665" checked>' in text
    assert 'name="never"\n               value="660">' in text


async def test_pick_adds_way_and_new_pin(client: Client) -> None:
    """A picked way is added; a new pin needs only From and To."""
    query = urlencode([
        ('from', 'G2145615'), ('to', '214510'), ('pick', 'G215254,2560'),
    ])
    text = await (await client.get(f'/pins/ways?{query}')).text()
    assert '<title>Add a connection - paperroute</title>' in text
    assert 'value="G215254,2560,' in text
    assert 'action="/pins/delete"' not in text


async def test_refusals(pins: Pins, client: Client) -> None:
    """Unknown stops, bad windows and gone refs are refused."""
    response = await client.post(
        '/pins/ways/save', data=[('from', 'nowhere'), ('to', '214510')],
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert MISFIT in await response.text()
    data = [
        *(item for item in SAVE if item[0] != 'wn'),
        ('wn', '1'), ('w0_from', '07:00'), ('w0_to', '08:00'),
    ]
    response = await client.post('/pins/ways/save', data=data)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert NO_DAY in await response.text()
    gone = await client.get('/pins/ways?ref=none', allow_redirects=False)
    assert gone.headers['Location'] == '/pins?done=gone'
    start = await (await client.get('/pins/ways')).text()
    assert '<form class="search"' not in start
    assert 'placeholder="Pick a stop">' in start
    assert unlabelled(text=start) == []
    unknown = await (await client.get('/pins/ways?from_q=Nowhere')).text()
    assert 'No stop or station matches “Nowhere”.' in unknown
    assert pins.posted == []


async def test_before_timetable(pins: Pins) -> None:
    """Before the timetable is ready the page says so; saves go back."""
    display = WebDisplay(device=Device().pages(), pins=pins.get, options=None)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    text = await (await client.get(f'/pins/ways?ref={REF}')).text()
    response = await client.post(
        '/pins/ways/save', data=SAVE, allow_redirects=False,
    )
    script = await (await client.get('/ways.js')).text()
    await client.close()
    assert 'Starting up. Reload in a moment.' in text
    assert response.headers['Location'] == '/pins'
    assert 'export async function nextStep' in script


async def test_hand_way_carries_unsaved_edits(client: Client) -> None:
    """Add a way by hand takes the fields as edited to the hand editor."""
    edited = [
        *SAVE[:-2], ('never', '665'), ('nickname', 'Office'),
        ('wn', '1'), ('w0_day', 'mon'), ('w0_from', '07:00'),
        ('w0_to', '09:00'),
    ]
    response = await client.get(
        f'/pins/ways/hand?{urlencode([*edited, ("hand", "")])}',
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    location = urlsplit(response.headers['Location'])
    assert location.path == '/pins/connect'
    fields = dict(parse_qsl(location.query))
    assert fields['stop'] == 'G2145615'
    assert fields['to'] == '214510'
    assert parse_qsl(fields['back']) == edited


async def test_stop_search_reads_as_field_text(client: Client) -> None:
    """Tom Select's chosen stop uses the page's text and surface tokens."""
    text = await (await client.get(f'/pins/ways?ref={REF}')).text()
    sheet = await (await client.get('/live.css')).text()
    assert text.index('/vendor/tom-select.css') < text.index('/live.css')
    full = sheet.index('.ts-wrapper.full .ts-control,')
    assert 'background: var(--surface);' in sheet[full:full + 120]
    item = sheet.index('.ts-wrapper .ts-control .item,')
    assert 'color: var(--text);' in sheet[item:item + 120]


async def test_ways_page_puts_delete_in_its_own_card(client: Client) -> None:
    """A saved connection pin's Delete sits in a danger card."""
    text = await (await client.get(f'/pins/ways?ref={REF}')).text()
    check_danger_card(text=text, ref=REF, label=pin_label(pin=WORK))
    editor_end = text.index('</section>', text.index('pin-edit-heading'))
    assert text.index('>Delete pin</button>') > editor_end
