"""Tests for web pins: connections."""

import re
from collections.abc import AsyncIterator
from dataclasses import replace
from html import escape
from http import HTTPStatus

import pytest

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.core.connection_factories import WORK as CONNECTION
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.adapters.pin_editor import edited
from paperroute_transit.adapters.web_pin_edit import editor_form, line_field
from paperroute_transit.adapters.web_pins import pin_label, pin_line
from paperroute_transit.core.chooser import Choice
from paperroute_transit.core.messages import PinSaved
from paperroute_transit.core.model import StopPlace, pin_ref
from paperroute_transit.core.pin_choices import PinDraft
from paperroute_transit.observers import PinsSeen
from tests.adapters.web_pins.helpers import (
    ENDS,
    HOME,
    LOST,
    SAVED,
    Client,
    Pins,
    _client,
    _form,
    _options,
)


def between(*, text: str, start: str) -> str:
    """
    Cut a page from some markup to the end of its form.

    Parameters
    ----------
    text : str
        The page.
    start : str
        Markup where the cut starts.

    Returns
    -------
    str
        The text from `start` up to the next ``</form>``.
    """
    first = text.index(start)
    return text[first:text.index('</form>', first)]


async def test_connection_search_goes_to_ways_page(editor: Client) -> None:
    """An old connection search opens the ways page searching its From."""
    response = await editor.get(
        '/pins/new?q=Hollis&connect=1', allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins/ways?from_q=Hollis'


async def test_plain_search_unchanged(editor: Client) -> None:
    """Without connect, results still link to the pin editor."""
    text = await (await editor.get('/pins/new?q=Hollis')).text()
    assert 'href="/pins/edit?stop=C"' in text
    assert 'name="connect"' not in text


async def test_connection_row_edit_link(pins: Pins) -> None:
    """A connection pin's row edits it on the ways page."""
    pins.seen = PinsSeen(pins=(CONNECTION, HOME), shown=0, revision=3)
    client = await _client(pins=pins)
    text = await (await client.get('/pins')).text()
    await client.close()
    ref = pin_ref(pin=CONNECTION)
    assert f'href="/pins/ways?ref={ref}"' in text
    line = pin_line(pin=CONNECTION, end_points=ENDS)
    assert line == (
        'Hollis St Opp Constitution Hill Library → Westmead Station'
        ' · via Constitution Hill, North West Twy'
    )
    assert f'<small class="trip">{escape(line)}</small>' in text


async def test_destination_row_line_is_cut(pins: Pins) -> None:
    """A pin with a To but no ways has its line cut, like a connection."""
    direct = replace(CONNECTION, ways=())
    pins.seen = PinsSeen(pins=(direct, HOME), shown=0, revision=3)
    client = await _client(pins=pins)
    text = await (await client.get('/pins')).text()
    await client.close()
    line = escape(pin_line(pin=direct, end_points=ENDS))
    assert f'<small class="trip">{line}</small>' in text
    assert text.count('class="trip"') == 1


async def test_pins_page_adds_connection(client: Client) -> None:
    """The pins page opens the ways page for a new connection."""
    text = await (await client.get('/pins')).text()
    form = between(text=text, start='<form method="get" action="/pins/ways">')
    assert (
        '<button type="submit" class="secondary">Add a connection</button>'
        in form
    )


async def test_search_title(editor: Client) -> None:
    """A search page is titled for a pin."""
    plain = await (await editor.get('/pins/new?q=Hollis')).text()
    assert '<title>Add a pin' in plain


def test_line_field_posts_the_value_and_shows_the_badge() -> None:
    """A named line's option holds its value and reads as its badge."""
    lines = (
        Choice(key='k1', label='GA', badge='GA', value='GA:Norwich & Co'),
        Choice(key='k2', label='GA', badge='GA', value='GA:Liverpool'),
    )
    found = edited(
        options=FakePinOptions(
            places={'S': StopPlace(stop_id='S', name='Sta', point=None)},
            lines={('S', SydneyMode.METRO): lines},
        ),
        draft=PinDraft(stop='S', kind=SydneyMode.METRO),
    )
    text = line_field(editor=found, modes=SYDNEY.modes)
    assert re.findall(r'<option value="([^"]*)"', text) == [
        '', 'GA:Norwich &amp; Co', 'GA:Liverpool',
    ]
    assert re.findall(r'>([^<]*)</option>', text)[1:] == ['GA', 'GA']


def test_sydney_metro_editor_keeps_line_then_to() -> None:
    """A metro editor still asks for the line before the To."""
    found = edited(
        options=_options(),
        draft=PinDraft(stop='C', kind='metro', route='T1'),
        modes=SYDNEY.modes,
    )
    text = editor_form(
        editor=found, origin='Central', windows='', modes=SYDNEY.modes,
    )
    assert text.index('name="route"') < text.index('name="to"')
    assert text.index('Pick a line') < text.index('name="to"')


def test_the_editor_heading_escapes_the_from() -> None:
    """A From with markup characters is escaped in the heading."""
    found = edited(
        options=_options(), draft=PinDraft(stop='C', kind='metro'),
        modes=SYDNEY.modes,
    )
    text = editor_form(
        editor=found, origin='A & <B>', windows='', modes=SYDNEY.modes,
    )
    assert '<h3>A &amp; &lt;B&gt;</h3>' in text
    assert '<B>' not in text


@pytest.fixture(name='bus_editor')
async def _bus_editor(pins: Pins) -> AsyncIterator[Client]:
    """A client whose only stop has one bus line."""
    stop = StopPlace(stop_id='B1', name='Bus stop', point=None)
    client = await _client(
        pins=pins,
        options=FakePinOptions(
            places={'B1': stop},
            lines={('B1', SydneyMode.BUS): (
                Choice(key='b7', label='Seven', badge='7'),
            )},
        ),
    )
    yield client
    await client.close()


async def test_save_without_a_route_is_still_refused(
    pins: Pins, bus_editor: Client,
) -> None:
    """An empty route with no suggestion is refused."""
    response = await bus_editor.post(
        '/pins/save', data=_form({'stop': 'B1', 'kind': 'bus', 'route': ''}),
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not pins.posted


async def test_save_with_a_stale_suggestion_is_still_refused(
    pins: Pins, bus_editor: Client,
) -> None:
    """A train line posted after a switch to Bus is not swapped in."""
    response = await bus_editor.post('/pins/save', data=_form({
        'stop': 'B1', 'kind': 'bus', 'route': 'T1',
    }))
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not pins.posted


async def test_edit_keeps_the_kerb_across(pins: Pins, editor: Client) -> None:
    """Saving a both-sides pin's nickname keeps its kerb across."""
    across = StopPlace(stop_id='B3', name='Across', point=(1.0, 2.0))
    both = replace(
        SAVED, destination=None, opposite=across, towards=('Town', 'Bay'),
    )
    pins.seen = PinsSeen(pins=(both, LOST), shown=0, revision=3)
    ref = pin_ref(pin=both)
    response = await editor.post('/pins/save', data=_form({
        'ref': ref, 'stop': 'B2', 'kind': 'bus', 'route': '811',
        'nickname': 'Shopping',
    }), allow_redirects=False)
    assert response.status == HTTPStatus.SEE_OTHER
    assert pins.posted == [
        PinSaved(ref=ref, pin=replace(both, nickname='Shopping')),
    ]


def test_pin_label_names_both_sides() -> None:
    """A both-sides pin without a nickname is listed as both sides."""
    both = replace(HOME, opposite=SAVED.origin)
    assert pin_label(pin=both) == 'Home Stop, both sides'
    assert pin_line(pin=both, end_points=ENDS) == 'Home Stop, both sides'
