"""Tests for web pins: editor."""

import re
from collections.abc import AsyncIterator
from dataclasses import replace
from http import HTTPStatus

import pytest

from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_testkit.page_markup import inside, parse_tree
from paperroute_transit.adapters.web_pins import draft_of, pin_label
from paperroute_transit.core.chooser import Choice
from paperroute_transit.core.messages import PinSaved
from paperroute_transit.core.model import Pin, StopPlace, pin_ref
from paperroute_transit.observers import PinsSeen
from tests.adapters.web_pins.helpers import (
    BUS_STOP,
    CENTRAL,
    FULL,
    JUNCTION,
    LOST,
    REDFERN,
    SAVED,
    WORK,
    Client,
    Pins,
    _client,
    _form,
    _options,
    check_danger_card,
    delete_button,
)


def test_draft_of_reads_fields() -> None:
    """Fields are read and an unknown kind is dropped."""
    draft = draft_of(
        query={'stop': 'C', 'kind': 'boat', 'ref': 'x'}, only=['T1'],
        modes=SYDNEY.modes,
    )
    assert (draft.stop, draft.kind, draft.ref) == ('C', None, 'x')
    assert draft.only == ('T1',)
    assert draft_of(
        query={'kind': 'metro'},
        only=[],
        modes=SYDNEY.modes,
    ).kind == SydneyMode.METRO


async def test_search_lists_matches(editor: Client) -> None:
    """A match links to its editor with its kinds and routes."""
    text = await (await editor.get('/pins/new?q=c%20st')).text()
    assert 'href="/pins/edit?stop=C"' in text
    assert '<small>Metro</small>' in text
    assert '<span class="routes">T1 T2</span>' in text


async def test_search_too_short(editor: Client) -> None:
    """One letter is not enough."""
    text = await (await editor.get('/pins/new?q=c')).text()
    assert 'Type at least 2 letters.' in text


async def test_search_without_hits(pins: Pins) -> None:
    """No hits says so, quoting the text."""
    client = await _client(pins=pins, options=FakePinOptions())
    text = await (await client.get('/pins/new?q=zz%3C')).text()
    await client.close()
    assert 'No stop or station matches “zz&lt;”.' in text


async def test_editor_offers_valid_choices(editor: Client) -> None:
    """Only the fake's To places and filters are offered."""
    response = await editor.get('/pins/edit?stop=C&kind=metro&route=T1')
    text = await response.text()
    assert re.findall(r'<option value="(\w*)"', text) == [
        '', 'T1', 'T2', '', 'J', 'R',
    ]
    assert re.findall(r'name="only" value="(\w+)"', text) == ['T1', 'T2']
    assert 'Leave all unticked to show every route.' in text
    assert 'maxlength="24"' in text
    assert 'formaction="/pins/save"' in text
    assert '<legend>Mode</legend>' in text


async def test_editor_line_without_a_to_says_it_sets_the_strip(
    editor: Client,
) -> None:
    """With no To the line select is labelled as the Strip's, not a filter."""
    plain = await (
        await editor.get('/pins/edit?stop=C&kind=metro&route=T1')
    ).text()
    assert '>Strip line<' in plain
    assert 'aria-describedby="pin-route-hint"' in plain
    assert 'the stop lists every route' in plain


async def test_editor_line_with_a_to_is_labelled_line(
    editor: Client,
) -> None:
    """With a To the line select is plainly the Line, with no hint."""
    text = await (
        await editor.get('/pins/edit?stop=C&kind=metro&route=T1&to=J')
    ).text()
    assert '>Line<' in text
    assert 'pin-route-hint' not in text


async def test_editor_without_line_has_no_save(editor: Client) -> None:
    """Save waits for a line."""
    text = await (await editor.get('/pins/edit?stop=C')).text()
    assert 'formaction="/pins/save"' not in text
    assert 'Show choices' in text


async def test_editor_drops_unoffered_to(editor: Client) -> None:
    """A To not offered falls back to every departure."""
    response = await editor.get(
        '/pins/edit?stop=C&kind=metro&route=T1&to=ZZ',
    )
    text = await response.text()
    assert '<option value="" selected>None: every departure</option>' in text


async def test_save_posts_pin(pins: Pins, editor: Client) -> None:
    """A full form saves the pin and returns to the list."""
    response = await editor.post(
        '/pins/save', data=_form(FULL), allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.headers['Location'] == '/pins?seen=3&done=saved'
    assert pins.posted == [PinSaved(ref=None, pin=Pin(
        origin=CENTRAL,
        destination=JUNCTION,
        kind=SydneyMode.METRO,
        routes=('T1',),
        only=('T2',),
        nickname='Home',
    ))]


TRAIN_STOP = StopPlace(stop_id='S1', name='Strathfield', point=None)


TRAIN_LINES = (
    Choice(key='T1', label='North Shore', badge='T1'),
    Choice(key='T2', label='Leppington', badge='T2'),
)


@pytest.fixture(name='trains')
async def _trains(pins: Pins) -> AsyncIterator[Client]:
    """A client whose timetable has a train station and a bus stop."""
    pins.seen = PinsSeen(pins=(), shown=0, revision=3)
    options = replace(
        _options(),
        places={'S1': TRAIN_STOP, 'J': JUNCTION, 'R': REDFERN, 'B2': BUS_STOP},
        lines={
            ('S1', SydneyMode.TRAIN): TRAIN_LINES,
            ('B2', SydneyMode.BUS): TRAIN_LINES,
        },
        reached_by={'S1': (JUNCTION, REDFERN)},
    )
    client = await _client(pins=pins, options=options)
    yield client
    await client.close()


async def test_the_train_form_offers_no_line(trains: Client) -> None:
    """A train's editor has a Destination and no route select."""
    text = await (
        await trains.get('/pins/edit?stop=S1&kind=train')
    ).text()
    assert 'name="route"' not in text
    assert 'name="to"' in text
    assert 'formaction="/pins/save"' in text


async def test_a_train_pin_needs_no_line(
    pins: Pins, trains: Client,
) -> None:
    """A train form posted with a From and no line saves no routes."""
    response = await trains.post(
        '/pins/save', data=_form({'stop': 'S1', 'kind': 'train'}),
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert pins.posted == [PinSaved(ref=None, pin=Pin(
        origin=TRAIN_STOP, kind=SydneyMode.TRAIN, routes=(),
    ))]


async def test_a_train_pin_saves_its_destination(
    pins: Pins, trains: Client,
) -> None:
    """A Destination the From reaches is saved with no routes."""
    await trains.post(
        '/pins/save',
        data=_form({'stop': 'S1', 'kind': 'train', 'to': 'R'}),
        allow_redirects=False,
    )
    assert pins.posted == [PinSaved(ref=None, pin=Pin(
        origin=TRAIN_STOP, destination=REDFERN, kind=SydneyMode.TRAIN,
        routes=(),
    ))]


async def test_an_old_train_pin_opens_with_its_from_and_to(
    pins: Pins, trains: Client,
) -> None:
    """A stored train line is not shown, and saving drops it."""
    old = Pin(
        origin=TRAIN_STOP, destination=REDFERN, kind=SydneyMode.TRAIN,
        routes=('T2',),
    )
    pins.seen = PinsSeen(pins=(old,), shown=0, revision=3)
    ref = pin_ref(pin=old)
    text = await (await trains.get(f'/pins/edit?ref={ref}')).text()
    assert 'name="route"' not in text
    assert '<option value="R" selected>' in text
    response = await trains.post(
        '/pins/save',
        data=_form({
            'ref': ref, 'stop': 'S1', 'kind': 'train', 'to': 'R',
        }),
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert pins.posted == [PinSaved(ref=ref, pin=replace(old, routes=()))]


async def test_a_bus_pin_still_needs_its_route(
    pins: Pins, trains: Client,
) -> None:
    """A bus form without a route is refused, and has a route select."""
    text = await (await trains.get('/pins/edit?stop=B2&kind=bus')).text()
    assert 'name="route"' in text
    response = await trains.post(
        '/pins/save', data=_form({'stop': 'B2', 'kind': 'bus'}),
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not pins.posted


async def test_save_refuses_unoffered_to(
    pins: Pins, editor: Client,
) -> None:
    """A To not offered is refused."""
    response = await editor.post(
        '/pins/save', data=_form({**FULL, 'to': 'ZZ'}),
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert "Those choices don't fit together. Pick again." in (
        await response.text()
    )
    assert not pins.posted


async def test_save_refuses_route_not_at_stop(
    pins: Pins, editor: Client,
) -> None:
    """A train line at a bus stop is refused."""
    response = await editor.post('/pins/save', data=_form({
        'stop': 'B2', 'kind': 'metro', 'route': 'T1',
    }))
    assert response.status == HTTPStatus.BAD_REQUEST
    assert not pins.posted


async def test_save_of_duplicate_says_so(
    pins: Pins, editor: Client,
) -> None:
    """A new pin the same as another is not saved."""
    response = await editor.post('/pins/save', data=_form({
        'stop': 'B2', 'kind': 'bus', 'route': '811', 'to': 'TH',
    }), allow_redirects=False)
    assert response.headers['Location'] == '/pins?done=duplicate'
    assert not pins.posted


async def test_edit_existing_prefills(pins: Pins, editor: Client) -> None:
    """A pin's editor starts from it and saves over it."""
    ref = pin_ref(pin=SAVED)
    text = await (await editor.get(f'/pins/edit?ref={ref}')).text()
    assert 'value="bus" checked' in text
    assert '<option value="811" selected>' in text
    assert '<option value="TH" selected>' in text
    assert 'value="Shops"' in text
    assert f'name="ref" value="{ref}"' in text
    response = await editor.post('/pins/save', data=_form({
        'ref': ref, 'stop': 'B2', 'kind': 'bus', 'route': '811',
        'to': 'TH', 'nickname': 'Shopping',
    }), allow_redirects=False)
    assert response.status == HTTPStatus.SEE_OTHER
    assert pins.posted == [
        PinSaved(ref=ref, pin=replace(SAVED, nickname='Shopping')),
    ]


async def test_edit_page_offers_delete(editor: Client) -> None:
    """A saved pin's editor ends with a danger Delete for that pin."""
    ref = pin_ref(pin=SAVED)
    text = await (await editor.get(f'/pins/edit?ref={ref}')).text()
    nodes = parse_tree(text=text)
    form = next(
        node for node in nodes
        if node.tag == 'form' and node.attrs.get('action') == '/pins/delete'
    )
    held = inside(nodes=nodes, outer=form)
    button = delete_button(held=held, ref=ref)
    assert button.attrs['data-label'] == pin_label(pin=SAVED)
    editor_form = next(
        node for node in nodes
        if node.tag == 'form' and 'editor' in (node.attrs.get('class') or '')
    )
    assert form.index > max(inside(nodes=nodes, outer=editor_form)).index
    assert 'id="delete-dialog"' in text


async def test_new_pin_editor_has_no_delete(editor: Client) -> None:
    """A pin not yet saved has nothing to delete."""
    text = await (await editor.get('/pins/edit?stop=B2')).text()
    assert '/pins/delete' not in text


async def test_edit_page_puts_delete_in_its_own_card(editor: Client) -> None:
    """A saved pin's Delete sits in a danger card after the editor."""
    ref = pin_ref(pin=SAVED)
    text = await (await editor.get(f'/pins/edit?ref={ref}')).text()
    check_danger_card(text=text, ref=ref, label=pin_label(pin=SAVED))


async def test_new_pin_has_no_delete_card(editor: Client) -> None:
    """The Add a pin page has no danger card."""
    text = await (await editor.get('/pins/edit?stop=B2')).text()
    assert 'Add a pin' in text
    assert 'danger-zone' not in text


async def test_delete_confirm_cancels_to_editor(client: Client) -> None:
    """Cancel on the confirm page goes back to that pin's editor."""
    ref = pin_ref(pin=WORK)
    asked = await client.post('/pins/delete', data={'ref': ref})
    assert f'href="/pins/edit?ref={ref}">Cancel' in await asked.text()


async def test_edit_of_unknown_ref_is_gone(editor: Client) -> None:
    """An unknown ref goes back to the list."""
    response = await editor.get(
        '/pins/edit?ref=nope', allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins?done=gone'


async def test_missing_pin_lists_nearby(editor: Client) -> None:
    """A missing pin's editor links to stops nearby with its ref."""
    ref = pin_ref(pin=LOST)
    text = await (await editor.get(f'/pins/edit?ref={ref}')).text()
    assert 'Stops nearby' in text
    assert f'href="/pins/edit?ref={ref}&amp;stop=B2"' in text
    assert 'Old Stop' in text


async def test_resave_of_missing_pin_lists_nearby(
    pins: Pins, editor: Client,
) -> None:
    """Saving a missing pin as it was offers stops nearby, not a 400."""
    ref = pin_ref(pin=LOST)
    response = await editor.post('/pins/save', data=_form({
        'ref': ref, 'stop': 'OLD', 'kind': 'bus', 'route': '809',
    }))
    assert response.status == HTTPStatus.OK
    assert 'Stops nearby' in await response.text()
    assert not pins.posted


async def test_search_form_is_a_search_landmark(editor: Client) -> None:
    """The search form sits in a search element."""
    text = await (await editor.get('/pins/new?q=c')).text()
    assert '<search>' in text
