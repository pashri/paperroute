"""Tests for the pin pages and their suggestion plug."""

import re
from collections.abc import Callable
from http import HTTPStatus

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import MultiMapping

from paperroute.adapters.board_head import BoardHead
from paperroute.adapters.board_pages import PagePlug, PinPages
from paperroute.adapters.web_board import PINS_NAV
from paperroute.adapters.web_display import WebDisplay
from paperroute.adapters.web_ways import WaysPages
from paperroute.city import SYDNEY
from paperroute_device.adapters.web_frame import LIVE_NAV
from paperroute_testkit.core.connection_factories import WORK
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_transit.core.messages import Event
from paperroute_transit.core.model import pin_ref
from tests.adapters.test_connection_editor import OPTIONS
from tests.adapters.test_web_pin_search import MATCH
from tests.adapters.test_web_pins import Pins

Client = TestClient[web.Request, web.Application]
PLUGGED = '<p id="plugged">'
SUGGEST_LINE = '  <script type="module" src="/suggest.js"></script>\n'
WAYS_LINE = '  <script type="module" src="/ways.js"></script>\n'


class PluggedCard:
    """A card that only marks where it is drawn."""

    def card(self, query: MultiMapping[str]) -> str:
        """
        Draw the mark.

        Parameters
        ----------
        query : MultiMapping[str]
            Ignored.

        Returns
        -------
        str
            The mark.
        """
        return PLUGGED

    def routes(self) -> list[web.RouteDef]:
        """
        List no routes.

        Returns
        -------
        list[web.RouteDef]
            None.
        """
        return []


class RecordingPlug:
    """A plug that hands out the marking card and counts its closes."""

    def __init__(self) -> None:
        self.closes = 0

    def pages(
        self, *, ways: WaysPages, skip_school: Callable[[], bool],
    ) -> PagePlug:
        """
        Give the marking card.

        Parameters
        ----------
        ways : WaysPages
            Ignored.
        skip_school : Callable[[], bool]
            Ignored.

        Returns
        -------
        PagePlug
            The card.
        """
        return PluggedCard()

    async def close(self) -> None:
        """Count the close."""
        self.closes += 1


async def started(*, board: PinPages) -> Client:
    """
    Start a client of a display serving the pin pages.

    Parameters
    ----------
    board : PinPages
        The pin pages.

    Returns
    -------
    Client
        The started client.
    """
    client: Client = TestClient(
        TestServer(WebDisplay(board=board, city=SYDNEY).app()),
    )
    await client.start_server()
    return client


async def test_pin_pages_without_plug_serve_no_suggest() -> None:
    """With no plug the pin pages answer and suggestions are not found."""
    pins = Pins(pins=(WORK,))
    board = PinPages(
        head=BoardHead(),
        pins=pins.get, options=None, modes=SYDNEY.modes, plug=None,
    )
    board.pins_sleep = pins.sleep
    client = await started(board=board)
    try:
        listed = await client.get('/pins')
        suggested = await client.get('/pins/ways/suggest')
    finally:
        await client.close()
    assert listed.status == HTTPStatus.OK
    assert suggested.status == HTTPStatus.NOT_FOUND


async def test_plug_card_on_ways_page() -> None:
    """The plug's card is drawn on the ways page; shutdown closes it once."""
    pins = Pins(pins=(WORK,))
    plug = RecordingPlug()
    board = PinPages(
        head=BoardHead(),
        pins=pins.get, options=OPTIONS, modes=SYDNEY.modes, plug=plug,
    )
    board.pins_sleep = pins.sleep
    client = await started(board=board)
    try:
        text = await (
            await client.get(f'/pins/ways?ref={pin_ref(pin=WORK)}')
        ).text()
    finally:
        await client.close()
    assert PLUGGED in text
    assert plug.closes == 1


async def test_pin_pages_follow_reported_school_switch() -> None:
    """The stop search leaves out school routes only when reported on."""
    options = FakePinOptions(found=(MATCH,))
    reported = {'skip_school_buses': 'on'}
    posted: list[Event] = []
    board = PinPages(
        head=BoardHead(),
        pins=lambda: None, options=options, modes=SYDNEY.modes, plug=None,
    )
    app = web.Application()
    app.add_routes(board.routes(send=posted.append, reported=lambda: reported))
    client: Client = TestClient(TestServer(app))
    await client.start_server()
    try:
        await client.get('/pins/stops.json?q=parra')
        reported['skip_school_buses'] = 'off'
        await client.get('/pins/stops.json?q=parra')
    finally:
        await client.close()
    assert options.skipped == [True, False]
    assert not posted


async def test_pin_pages_close_without_a_plug() -> None:
    """Closing pages with no plug does nothing."""
    await PinPages(
        head=BoardHead(),
        pins=lambda: None, options=None, modes=SYDNEY.modes, plug=None,
    ).close()


def nav_links(*, page: str) -> list[tuple[str, bool]]:
    """
    List a page's nav links.

    Parameters
    ----------
    page : str
        The page's HTML.

    Returns
    -------
    list[tuple[str, bool]]
        Each link's href, and whether it is marked current.
    """
    found = re.search(r'<nav\b.*?</nav>', page, flags=re.DOTALL)
    assert found is not None
    return [
        (href, bool(mark))
        for href, mark in re.findall(
            r'<a href="([^"]*)"( aria-current="page")?>', found.group(0),
        )
    ]


async def test_scripts_follow_the_plug() -> None:
    """Only a plug names the suggestion script and serves it."""
    for plug in (RecordingPlug(), None):
        board = PinPages(
            head=BoardHead(),
            pins=lambda: None, options=OPTIONS, modes=SYDNEY.modes,
            plug=plug,
        )
        client = await started(board=board)
        try:
            page = await (await client.get('/pins/new')).text()
            script = await client.get('/suggest.js')
        finally:
            await client.close()
        if plug is not None:
            assert SUGGEST_LINE in page
            assert script.status == HTTPStatus.OK
            assert script.content_type == 'text/javascript'
        else:
            assert 'suggest.js' not in page
            assert f'{WAYS_LINE}</head>' in page
            assert script.status == HTTPStatus.NOT_FOUND


async def test_board_nav_comes_from_the_city() -> None:
    """The pins page and the editor list the city's links, Pins current."""
    pins = Pins(pins=(WORK,))
    board = PinPages(
        head=BoardHead(nav=(LIVE_NAV, PINS_NAV)),
        pins=pins.get, options=OPTIONS, modes=SYDNEY.modes, plug=None,
    )
    board.pins_sleep = pins.sleep
    client = await started(board=board)
    try:
        pages = [
            await (await client.get(path)).text()
            for path in ('/pins', '/pins/new')
        ]
    finally:
        await client.close()
    for page in pages:
        assert nav_links(page=page) == [('/', False), ('/pins', True)]
