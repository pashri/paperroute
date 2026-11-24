"""Tests for web pins: windows and sheets."""

import re
from dataclasses import replace
from datetime import time
from http import HTTPStatus

from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_testkit.page_markup import parse_page
from paperroute_transit.core.messages import PinSaved
from paperroute_transit.core.model import AutoWindow, pin_ref
from paperroute_transit.core.route_colours import RouteColour
from paperroute_transit.observers import PinsSeen
from tests.adapters.web_pins.helpers import (
    FULL,
    SAVED,
    Client,
    Pins,
    _client,
    _form,
)

MORNING = AutoWindow(days=frozenset({0}), start=time(7, 30), end=time(9))


ROW_FIELDS: dict[str, str | list[str]] = {
    'wn': '1', 'w0_day': 'mon', 'w0_from': '07:30', 'w0_to': '09:00',
}


async def test_editor_shows_pin_windows(
    pins: Pins, editor: Client,
) -> None:
    """A pin's editor starts from its windows."""
    windowed = replace(SAVED, windows=(MORNING,))
    pins.seen = PinsSeen(pins=(windowed,), shown=0, revision=3)
    ref = pin_ref(pin=windowed)
    text = await (await editor.get(f'/pins/edit?ref={ref}')).text()
    assert 'name="w0_day" value="mon" checked' in text
    assert 'name="w0_day" value="tue" checked' not in text
    assert 'name="w0_from" value="07:30"' in text


async def test_save_posts_windows(pins: Pins, editor: Client) -> None:
    """A save with a row posts the pin with its window."""
    response = await editor.post(
        '/pins/save', data=_form({**FULL, **ROW_FIELDS}),
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    saved = pins.posted[0]
    assert isinstance(saved, PinSaved)
    assert saved.pin.windows == (MORNING,)


async def test_save_refuses_equal_times(
    pins: Pins, editor: Client,
) -> None:
    """Equal times are refused with the row kept."""
    response = await editor.post('/pins/save', data=_form({
        **FULL, **ROW_FIELDS, 'w0_to': '07:30',
    }))
    text = await response.text()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert (
        '<p class="notice" role="alert">'
        "A window can't start and end at the same time.</p>"
    ) in text
    assert 'name="w0_day" value="mon" checked' in text
    assert 'name="w0_to" value="07:30"' in text
    assert not pins.posted


async def test_add_window_without_js(editor: Client) -> None:
    """Add window redraws the editor with its fields and one more row."""
    response = await editor.get('/pins/edit', params=_form({
        **FULL, **ROW_FIELDS, 'add_window': '1',
    }))
    text = await response.text()
    assert 'name="w0_from" value="07:30"' in text
    assert 'name="w1_from" value=""' in text
    assert 'name="wn" value="2"' in text
    assert 'value="Home"' in text
    assert '<option value="T1" selected>' in text


async def test_enter_uses_default_button(editor: Client) -> None:
    """The form's first submit button is the off-screen default."""
    text = await (await editor.get(
        '/pins/edit?stop=C&kind=metro&route=T1',
    )).text()
    form = text[text.index('<form class="editor"'):]
    first = re.search(r'<button type="submit"[^>]*>', form)
    assert first is not None
    assert 'class="default-submit"' in first.group(0)
    assert 'tabindex="-1"' in first.group(0)
    assert 'aria-hidden="true"' in first.group(0)


async def test_list_has_chips(pins: Pins) -> None:
    """A pin's chip carries its route's colour class."""
    colours = {'606': RouteColour(fill='00b5ef', text='000000')}
    client = await _client(pins=pins, options=FakePinOptions(colours=colours))
    text = await (await client.get('/pins')).text()
    await client.close()
    assert 'class="chip mode-any c-00b5ef-000000"' in text


async def test_colour_sheet_is_served(pins: Pins) -> None:
    """The sheet holds a rule per colour of the pins' chip routes."""
    colours = {'606': RouteColour(fill='00b5ef', text='000000')}
    client = await _client(pins=pins, options=FakePinOptions(colours=colours))
    response = await client.get('/route-colours.css')
    text = await response.text()
    await client.close()
    assert response.content_type == 'text/css'
    assert response.headers['Cache-Control'] == 'no-store'
    assert '.chip.c-00b5ef-000000 { background: #00b5ef;' in text


async def test_colour_sheet_without_timetable(pins: Pins) -> None:
    """No report and no colours give an empty sheet."""
    pins.seen = None
    client = await _client(pins=pins, options=FakePinOptions())
    response = await client.get('/route-colours.css')
    text = await response.text()
    await client.close()
    assert response.status == HTTPStatus.OK
    assert text == ''


async def test_pins_page_links_colour_sheet(client: Client) -> None:
    """The Pins page links the colour sheet after the main one."""
    page = parse_page(text=await (await client.get('/pins')).text())
    assert page.stylesheets == [
        '/live.css', '/board.css', '/route-colours.css',
    ]
