"""Tests for the Pins list page."""

import re
from collections.abc import AsyncIterator
from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_display import WebDisplay
from paperroute.adapters.web_pins import (
    SEEN_POLL_S,
    SEEN_WAIT_S,
    pin_label,
    pin_line,
)
from paperroute.core.messages import Event, PinDeleted, PinMoved, PinsOrdered
from paperroute.core.model import Pin, StopPlace, pin_ref
from paperroute.observers import PinsSeen
from tests.adapters.test_web_device import Device

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


async def _client(*, pins: Pins) -> Client:
    """Start a client of a display wired to the fake pins."""
    display = WebDisplay(device=Device().pages(), pins=pins.get)
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


async def test_nav_has_pins_on_every_page(client: Client) -> None:
    """Every page lists Live, Pins, Settings, Device in order."""
    for path in ('/', '/settings', '/device', '/setup', '/pins'):
        response = await client.get(path)
        assert response.status == HTTPStatus.OK, path
        links = re.findall(
            r'<li><a href="([^"]+)"( aria-current="page")?>(\w+)</a></li>',
            await response.text(),
        )
        assert [(href, name) for href, _, name in links] == [
            ('/', 'Live'),
            ('/pins', 'Pins'),
            ('/settings', 'Settings'),
            ('/device', 'Device'),
        ], path
        if path != '/setup':
            current = [href for href, mark, _ in links if mark]
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
