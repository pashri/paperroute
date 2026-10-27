"""Tests for the Pins list page."""

import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import time
from html import escape
from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_display import WebDisplay
from paperroute.adapters.web_pins import (
    SEEN_POLL_S,
    SEEN_WAIT_S,
    draft_of,
    pin_label,
    pin_line,
)
from paperroute.core.chooser import Choice
from paperroute.core.messages import (
    Event,
    PinDeleted,
    PinMoved,
    PinSaved,
    PinsOrdered,
)
from paperroute.core.model import (
    AutoWindow,
    Pin,
    RouteKind,
    StopPlace,
    pin_ref,
)
from paperroute.core.pin_choices import StopMatch
from paperroute.core.route_colours import RouteColour
from paperroute.observers import PinsSeen
from tests.adapters.test_web_device import Device
from tests.core.connection_factories import WORK as CONNECTION
from tests.core.connection_factories import WORK_CHANGE
from tests.fake_pins import FakePinOptions
from tests.page_markup import inside, parse_page, parse_tree

Client = TestClient[web.Request, web.Application]


def _pin(
    *,
    name: str,
    stop_id: str,
    to: str | None = None,
    routes: tuple[str, ...] = (),
    nickname: str | None = None,
    missing: bool = False,
) -> Pin:
    """Build a pin between two named places."""
    return Pin(
        origin=StopPlace(stop_id=stop_id, name=name, point=None),
        destination=(
            None
            if to is None
            else StopPlace(stop_id=f'{stop_id}-to', name=to, point=None)
        ),
        routes=routes,
        nickname=nickname,
        missing=missing,
    )


WORK = _pin(
    name='Hollis St', stop_id='a', to='Parramatta', routes=('606',),
    nickname='Work',
)
HOME = _pin(name='Home Stop', stop_id='b')
ODD = _pin(name='<b>&', stop_id='c', missing=True)


class Pins:
    """Pins the app has reported, and the events posted."""

    def __init__(self, *, pins: tuple[Pin, ...], shown: int | None = 0):
        self.seen: PinsSeen | None = PinsSeen(
            pins=pins, shown=shown, revision=3,
        )
        self.posted: list[Event] = []
        self.slept: list[float] = []
        self.bump_after: float | None = None

    def get(self) -> PinsSeen | None:
        """Give the latest report."""
        return self.seen

    async def sleep(self, delay: float) -> None:
        """Pretend to wait, bumping the revision when told to."""
        self.slept.append(delay)
        if self.seen and self.bump_after is not None:
            if sum(self.slept) >= self.bump_after:
                self.seen = PinsSeen(
                    pins=(HOME,), shown=None,
                    revision=self.seen.revision + 1,
                )


async def _client(
    *, pins: Pins, options: FakePinOptions | None = None,
) -> Client:
    """Start a client of a display wired to the fake pins."""
    display = WebDisplay(
        device=Device().pages(), pins=pins.get, options=options,
    )
    display.bind(post=pins.posted.append)
    display.pins_sleep = pins.sleep
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


@pytest.fixture(name='pins')
def _pins() -> Pins:
    """Three pins, the first on screen."""
    return Pins(pins=(WORK, HOME, ODD))


@pytest.fixture(name='client')
async def _web_client(pins: Pins) -> AsyncIterator[Client]:
    """A client of the pins page."""
    client = await _client(pins=pins)
    yield client
    await client.close()


def test_pin_label_prefers_nickname() -> None:
    """The nickname leads, else the From name."""
    assert pin_label(pin=WORK) == 'Work'
    assert pin_label(pin=HOME) == 'Home Stop'


def test_pin_line_joins_from_to_and_routes() -> None:
    """The line has the To and routes only when there are some."""
    assert pin_line(pin=WORK) == 'Hollis St → Parramatta · 606'
    assert pin_line(pin=HOME) == 'Home Stop'


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


async def test_delete_needs_confirm(pins: Pins, client: Client) -> None:
    """Without confirm the page asks first; with it the pin goes."""
    ref = pin_ref(pin=WORK)
    asked = await client.post('/pins/delete', data={'ref': ref})
    assert asked.status == HTTPStatus.OK
    assert 'Delete “Work”?' in await asked.text()
    assert not pins.posted
    done = await client.post(
        '/pins/delete',
        data={'ref': ref, 'confirm': 'yes'},
        allow_redirects=False,
    )
    assert done.status == HTTPStatus.SEE_OTHER
    assert done.headers['Location'] == '/pins?seen=3&done=deleted'
    assert pins.posted == [PinDeleted(ref=ref)]


async def test_list_rows_are_one_line(client: Client) -> None:
    """Each row has named Move up, Move down and Edit, and no Delete."""
    text = await (await client.get('/pins')).text()
    nodes = parse_tree(text=text)
    rows = [
        node
        for node in nodes
        if node.tag == 'li' and 'data-ref' in node.attrs
    ]
    assert len(rows) == 3
    names_of = ('Work', 'Home Stop', '<b>&')
    for row, label in zip(rows, names_of, strict=True):
        held = inside(nodes=nodes, outer=row)
        names = [
            node.attrs.get('aria-label')
            for node in held
            if node.tag == 'button'
        ]
        assert names == [f'Move {label} up', f'Move {label} down']
        links = [node for node in held if node.tag == 'a']
        assert [link.text.strip() for link in links] == ['Edit']
    assert '/pins/delete' not in text
    labels = [label for label, _ in parse_page(text=text).buttons]
    assert 'Delete' not in labels


async def test_search_placeholder_is_short(client: Client) -> None:
    """The placeholder hints at what to type without repeating the label."""
    text = await (await client.get('/pins')).text()
    assert 'placeholder="Stop or station"' in text


async def test_list_button_tones(client: Client) -> None:
    """The move arrows are secondary; the confirm page's Delete is danger."""
    page = parse_page(text=await (await client.get('/pins')).text())
    tones = {text: classes for text, classes in page.buttons}
    assert 'secondary' in tones['↑']
    assert 'secondary' in tones['↓']
    asked = await client.post(
        '/pins/delete',
        data={'ref': pin_ref(pin=WORK)},
    )
    confirm = parse_page(text=await asked.text())
    assert ('Delete', frozenset({'danger'})) in confirm.buttons


async def test_delete_of_gone_pin_says_so(
    pins: Pins, client: Client
) -> None:
    """An unknown ref posts nothing."""
    response = await client.post(
        '/pins/delete',
        data={'ref': 'nope', 'confirm': 'yes'},
        allow_redirects=False,
    )
    assert response.headers['Location'] == '/pins?done=gone'
    assert not pins.posted


async def test_delete_without_ref_is_400(client: Client) -> None:
    """A missing ref is refused."""
    response = await client.post('/pins/delete')
    assert response.status == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize(('done', 'text'), [
    ('saved', 'Pin saved.'),
    ('deleted', 'Pin deleted.'),
    ('moved', 'Pins reordered.'),
    ('duplicate', 'That pin already exists.'),
])
async def test_done_notices(client: Client, done: str, text: str) -> None:
    """Each outcome has its notice."""
    page = await (await client.get(f'/pins?done={done}')).text()
    assert text in page
    other = await (await client.get('/pins?done=zzz')).text()
    assert 'class="notice"' not in other


async def test_get_waits_for_revision(pins: Pins, client: Client) -> None:
    """The page waits for a revision past `seen`, then draws it."""
    pins.bump_after = 0.1
    text = await (await client.get('/pins?seen=3')).text()
    assert 'Home Stop' in text
    assert '<b>Work</b>' not in text
    assert sum(pins.slept) < SEEN_WAIT_S


async def test_get_gives_up_waiting(pins: Pins, client: Client) -> None:
    """With no new revision it answers after SEEN_WAIT_S."""
    text = await (await client.get('/pins?seen=3')).text()
    assert '<b>Work</b>' in text
    assert sum(pins.slept) == pytest.approx(SEEN_WAIT_S)
    assert set(pins.slept) == {SEEN_POLL_S}


async def test_get_with_bad_seen_does_not_wait(
    pins: Pins, client: Client
) -> None:
    """A seen that is not a number is ignored."""
    await client.get('/pins?seen=x')
    assert not pins.slept


async def test_pins_js_is_served(client: Client) -> None:
    """The script is served as JavaScript."""
    response = await client.get('/pins.js')
    assert response.content_type == 'text/javascript'


async def test_windows_script_is_served(client: Client) -> None:
    """The preview script is served, and the editor page loads it."""
    response = await client.get('/windows.js')
    assert response.content_type == 'text/javascript'
    assert 'export function windowSummary' in await response.text()
    page = await client.get('/pins/edit?stop=C')
    assert '<script type="module" src="/windows.js">' in await page.text()


async def test_nav_has_pins_on_every_page(client: Client) -> None:
    """Every page lists Live, Pins, Settings, Device in order."""
    for path in ('/', '/settings', '/device', '/setup', '/pins'):
        response = await client.get(path)
        assert response.status == HTTPStatus.OK, path
        page = parse_page(text=await response.text())
        assert [(href, name) for href, _, name in page.links] == [
            ('/', 'Live'),
            ('/pins', 'Pins'),
            ('/settings', 'Settings'),
            ('/device', 'Device'),
        ], path
        if path != '/setup':
            current = [href for href, mark, _ in page.links if mark]
            assert current == [path], path


async def test_pins_routes_refuse_other_peers(pins: Pins) -> None:
    """A peer outside the local network is refused."""
    display = WebDisplay(pins=pins.get, remote_of=lambda request: '8.8.8.8')
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    try:
        response = await client.get('/pins')
        assert response.status == HTTPStatus.FORBIDDEN
    finally:
        await client.close()


async def test_pins_post_refuses_cross_site(
    pins: Pins, client: Client
) -> None:
    """A cross-site origin cannot move a pin."""
    response = await client.post(
        '/pins/move',
        data={'ref': pin_ref(pin=HOME), 'steps': '1'},
        headers={'Origin': 'http://evil.example'},
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not pins.posted


class Controls(HTMLParser):
    """Collects buttons outside any form, ignoring dialogs."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[str] = []
        self.dialog = 0
        self.loose = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Track forms and dialogs, and count loose buttons."""
        if tag == 'dialog':
            self.dialog += 1
        if tag == 'form':
            self.forms.append(dict(attrs).get('method') or 'get')
        if tag == 'button' and not self.dialog:
            self.loose += not self.forms

    def handle_endtag(self, tag: str) -> None:
        """Leave forms and dialogs."""
        if tag == 'dialog':
            self.dialog -= 1
        if tag == 'form':
            self.forms.pop()


async def test_page_works_without_script(client: Client) -> None:
    """Every control is a post form or a link."""
    parser = Controls()
    parser.feed(await (await client.get('/pins')).text())
    assert parser.loose == 0


CENTRAL = StopPlace(stop_id='C', name='Central', point=None)
JUNCTION = StopPlace(stop_id='J', name='Bondi Junction', point=None)
REDFERN = StopPlace(stop_id='R', name='Redfern', point=None)
BUS_STOP = StopPlace(stop_id='B2', name='Oxford St', point=None)
TOWN_HALL = StopPlace(stop_id='TH', name='Town Hall', point=None)
SAVED = Pin(
    origin=BUS_STOP,
    destination=TOWN_HALL,
    kind=RouteKind.BUS,
    routes=('811',),
    nickname='Shops',
)
LOST = Pin(
    origin=StopPlace(stop_id='OLD', name='Old Stop', point=(1.0, 2.0)),
    kind=RouteKind.BUS,
    routes=('809',),
    missing=True,
)


def _options() -> FakePinOptions:
    """C on T1/T2 and B2 on 809/811."""
    return FakePinOptions(
        places={place.stop_id: place for place in (
            CENTRAL, JUNCTION, REDFERN, BUS_STOP, TOWN_HALL,
        )},
        lines={
            ('C', RouteKind.TRAIN): (
                Choice(key='T1', label='North Shore', badge='T1'),
                Choice(key='T2', label='Leppington', badge='T2'),
            ),
            ('B2', RouteKind.BUS): (
                Choice(key='809', label='809', badge='809'),
                Choice(key='811', label='811', badge='811'),
            ),
        },
        to={
            ('C', 'T1'): (JUNCTION, REDFERN),
            ('B2', '811'): (TOWN_HALL,),
        },
        only={
            ('C', RouteKind.TRAIN): ('T1', 'T2'),
            ('B2', RouteKind.BUS): ('809', '811'),
        },
        near=(BUS_STOP,),
        found=(
            StopMatch(
                place=CENTRAL, kinds=(RouteKind.TRAIN,), routes=('T1', 'T2'),
            ),
        ),
    )


@pytest.fixture(name='editor')
async def _editor(pins: Pins) -> AsyncIterator[Client]:
    """A client whose display has the fake timetable."""
    pins.seen = PinsSeen(pins=(SAVED, LOST), shown=0, revision=3)
    client = await _client(pins=pins, options=_options())
    yield client
    await client.close()


FULL: dict[str, str | list[str]] = {
    'stop': 'C', 'kind': 'train', 'route': 'T1', 'to': 'J',
    'only': ['T2'], 'nickname': 'Home',
}


def _form(fields: dict[str, str | list[str]]) -> list[tuple[str, str]]:
    """Flatten form fields, repeating list values."""
    return [
        (name, item)
        for name, value in fields.items()
        for item in ([value] if isinstance(value, str) else value)
    ]


def test_draft_of_reads_fields() -> None:
    """Fields are read and an unknown kind is dropped."""
    draft = draft_of(
        query={'stop': 'C', 'kind': 'boat', 'ref': 'x'}, only=['T1'],
    )
    assert (draft.stop, draft.kind, draft.ref) == ('C', None, 'x')
    assert draft.only == ('T1',)
    assert draft_of(query={'kind': 'train'}, only=[]).kind is RouteKind.TRAIN


async def test_search_lists_matches(editor: Client) -> None:
    """A match links to its editor with its kinds and routes."""
    text = await (await editor.get('/pins/new?q=c%20st')).text()
    assert 'href="/pins/edit?stop=C"' in text
    assert '<small>Train</small>' in text
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
    response = await editor.get('/pins/edit?stop=C&kind=train&route=T1')
    text = await response.text()
    assert re.findall(r'<option value="(\w*)"', text) == [
        '', 'T1', 'T2', '', 'J', 'R',
    ]
    assert re.findall(r'name="only" value="(\w+)"', text) == ['T1', 'T2']
    assert 'Leave all unticked to show every route.' in text
    assert 'maxlength="24"' in text
    assert 'formaction="/pins/save"' in text
    assert '<legend>Mode</legend>' in text
    assert '>Line<' in text


async def test_editor_without_line_has_no_save(editor: Client) -> None:
    """Save waits for a line."""
    text = await (await editor.get('/pins/edit?stop=C')).text()
    assert 'formaction="/pins/save"' not in text
    assert 'Show choices' in text


async def test_editor_drops_unoffered_to(editor: Client) -> None:
    """A To not offered falls back to every departure."""
    response = await editor.get(
        '/pins/edit?stop=C&kind=train&route=T1&to=ZZ',
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
        kind=RouteKind.TRAIN,
        routes=('T1',),
        only=('T2',),
        nickname='Home',
    ))]


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
        'stop': 'B2', 'kind': 'train', 'route': 'T1',
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
    field = next(node for node in held if node.attrs.get('name') == 'ref')
    assert field.attrs['value'] == ref
    button = next(node for node in held if node.tag == 'button')
    assert button.text.strip() == 'Delete'
    assert button.attrs['class'] == 'danger'
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
        '/pins/edit?stop=C&kind=train&route=T1',
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
    assert page.stylesheets == ['/live.css', '/route-colours.css']


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


async def test_connection_search_links_to_connect(editor: Client) -> None:
    """A connection search links each result to the connection editor."""
    text = await (
        await editor.get('/pins/new?q=Hollis&connect=1')
    ).text()
    assert 'href="/pins/connect?stop=C"' in text
    assert '/pins/edit?' not in text
    form = between(text=text, start='<form class="search"')
    assert '<input type="hidden" name="connect" value="1">' in form
    assert 'value="Hollis"' in form


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
    line = pin_line(pin=CONNECTION)
    assert line == (
        'Hollis St Opp Constitution Hill Library → Westmead Station'
        ' · via Constitution Hill, North West Twy'
    )
    assert f'<small class="trip">{escape(line)}</small>' in text


async def test_pins_page_has_connection_search(client: Client) -> None:
    """The pins page has a labelled search for a connection's first stop."""
    text = await (await client.get('/pins')).text()
    form = between(text=text, start='<label for="connection-search">')
    assert (
        '<form class="search" method="get" action="/pins/new"'
        ' role="search">\n        <label for="connection-search">'
    ) in text
    assert 'Add a connection</label>' in form
    assert (
        '<input id="connection-search" type="search" name="q"'
        in form
    )
    assert 'placeholder="Leave from"' in form
    assert '<input type="hidden" name="connect" value="1">' in form
    assert '<button type="submit">Search</button>' in form


async def test_connection_search_title(editor: Client) -> None:
    """A connection search page is titled for a connection."""
    text = await (
        await editor.get('/pins/new?q=Hollis&connect=1')
    ).text()
    assert '<title>Add a connection' in text
    plain = await (await editor.get('/pins/new?q=Hollis')).text()
    assert '<title>Add a pin' in plain
