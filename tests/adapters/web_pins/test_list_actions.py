"""Tests for web pins: list actions."""

from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from aiohttp.test_utils import TestClient, TestServer

from paperroute_sydney.adapters.web_board import SuggestPlug
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.page_markup import inside, parse_page, parse_tree
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.adapters.web_pins import SEEN_POLL_S, SEEN_WAIT_S
from paperroute_transit.core.messages import PinDeleted
from paperroute_transit.core.model import pin_ref
from tests.adapters.transit_web import transit_display
from tests.adapters.web_pins.helpers import HOME, WORK, Client, Pins


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
    tones = dict(page.buttons)
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
    display = transit_display(
        board=PinPages(
            head=BoardHead(),
            pins=pins.get,
            options=None,
            modes=SYDNEY.modes,
            plug=SuggestPlug(planner=None),
        ),
        remote_of=lambda request: '8.8.8.8',
        city=SYDNEY,
    )
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
