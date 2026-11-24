"""Tests for web pins: pin list."""

from dataclasses import replace
from http import HTTPStatus

import pytest

from paperroute_sydney.city import SydneyMode
from paperroute_transit.adapters.web_pins import pin_label, pin_line
from paperroute_transit.core.messages import PinMoved, PinsOrdered
from paperroute_transit.core.model import Pin, StopPlace, pin_ref
from paperroute_transit.observers import PinsSeen
from tests.adapters.web_pins.helpers import ENDS, HOME, ODD, WORK, Client, Pins


def test_pin_label_prefers_nickname() -> None:
    """The nickname leads, else the From name."""
    assert pin_label(pin=WORK) == 'Work'
    assert pin_label(pin=HOME) == 'Home Stop'


def test_pin_line_without_a_to_names_only_its_filter() -> None:
    """A T1 pin without a To lists every line, so no line is named."""
    station = StopPlace(stop_id='P', name='Parramatta Station', point=None)
    line = Pin(origin=station, kind=SydneyMode.TRAIN, routes=('T1',))
    assert pin_line(pin=line, end_points=ENDS) == 'Parramatta Station'
    assert pin_line(pin=replace(line, only=('T2',)), end_points=ENDS) == (
        'Parramatta Station · T2'
    )


def test_pin_line_shows_the_badge_of_a_named_line() -> None:
    """A stored named line reads as its operator badge."""
    station = StopPlace(stop_id='P', name='Parramatta Station', point=None)
    pin = Pin(
        origin=station,
        destination=station,
        kind=SydneyMode.METRO,
        routes=('GA:Norwich',),
    )
    assert pin_line(pin=pin, end_points=ENDS) == (
        'Parramatta Station → Parramatta Station · GA'
    )


def test_pin_line_joins_from_to_and_routes() -> None:
    """The line has the To and routes only when there are some."""
    assert pin_line(pin=WORK, end_points=ENDS) == (
        'Hollis St → Parramatta · 606'
    )
    assert pin_line(pin=HOME, end_points=ENDS) == 'Home Stop'


async def test_list_shows_pins_in_order(client: Client) -> None:
    """Labels come in knob order and the shown pin is marked."""
    text = await (await client.get('/pins')).text()
    order = [text.index(x) for x in ('<b>Work</b>', 'Home Stop', '&lt;b')]
    assert order == sorted(order)
    assert 'Hollis St → Parramatta · 606' in text
    assert text.count('On the screen now') == 1
    assert text.count('Stop not in the new timetable') == 1


async def test_names_are_escaped(client: Client) -> None:
    """A name with markup shows as text."""
    text = await (await client.get('/pins')).text()
    assert '<b>&<' not in text
    assert '&lt;b&gt;&amp;' in text


async def test_list_without_pins_says_so(pins: Pins, client: Client) -> None:
    """An empty list invites a search."""
    pins.seen = PinsSeen(pins=(), shown=None, revision=1)
    text = await (await client.get('/pins')).text()
    assert 'No pins yet. Search for a stop or station to add one.' in text


async def test_list_before_first_report(pins: Pins, client: Client) -> None:
    """Before the app reports, the page says it is starting."""
    pins.seen = None
    text = await (await client.get('/pins')).text()
    assert 'Starting up. Reload in a moment.' in text


async def test_move_posts_event_and_redirects(
    pins: Pins, client: Client
) -> None:
    """A move posts the event and goes back to the list."""
    response = await client.post(
        '/pins/move',
        data={'ref': pin_ref(pin=HOME), 'steps': '-1'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?seen=3&done=moved'
    assert pins.posted == [PinMoved(ref=pin_ref(pin=HOME), steps=-1)]


async def test_move_of_gone_pin_says_so(
    pins: Pins, client: Client
) -> None:
    """An unknown ref posts nothing and the page explains."""
    response = await client.post(
        '/pins/move',
        data={'ref': 'nope', 'steps': '1'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?done=gone'
    assert not pins.posted
    text = await (await client.get('/pins?done=gone')).text()
    assert 'That pin changed on the device. Here are the pins now.' in text


@pytest.mark.parametrize('data', [
    {},
    {'ref': 'x'},
    {'ref': 'x', 'steps': 'up'},
])
async def test_move_with_bad_fields_is_400(
    client: Client, data: dict[str, str]
) -> None:
    """Missing or bad fields are refused."""
    response = await client.post('/pins/move', data=data)
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_move_before_first_report(pins: Pins, client: Client) -> None:
    """With no report yet, a move goes back to the page."""
    pins.seen = None
    response = await client.post(
        '/pins/move', data={'ref': 'x', 'steps': '1'}, allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins'
    assert not pins.posted


async def test_order_posts_refs(pins: Pins, client: Client) -> None:
    """The refs come back as a tuple in the posted order."""
    refs = [pin_ref(pin=p) for p in (ODD, WORK, HOME)]
    response = await client.post(
        '/pins/order',
        data={'refs': ','.join(refs)},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?seen=3&done=moved'
    assert pins.posted == [PinsOrdered(refs=tuple(refs))]


async def test_order_with_other_refs_says_gone(
    pins: Pins, client: Client
) -> None:
    """Refs that are not exactly the pins post nothing."""
    response = await client.post(
        '/pins/order', data={'refs': 'a,b'}, allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins?done=gone'
    assert not pins.posted


async def test_order_without_refs_is_400(client: Client) -> None:
    """A missing field is refused."""
    response = await client.post('/pins/order')
    assert response.status == HTTPStatus.BAD_REQUEST
