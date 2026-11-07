"""Tests for Sydney's board pages."""

import re
from importlib.resources import files

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute.adapters.web_board import SYDNEY_NAV, SydneyPages
from paperroute.core.messages import Event
from paperroute_device.adapters.web_frame import nav_html
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_testkit.fakes import FakeTripPlanner
from tests.adapters.test_web_pin_search import MATCH

Client = TestClient[web.Request, web.Application]


async def test_sydney_pages_follow_reported_school_switch() -> None:
    """The stop search leaves out school routes only when reported on."""
    options = FakePinOptions(found=(MATCH,))
    reported = {'skip_school_buses': 'on'}
    posted: list[Event] = []
    board = SydneyPages(pins=lambda: None, options=options, planner=None)
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


async def test_sydney_pages_close_the_planner() -> None:
    """Closing the pages closes the Trip Planner."""
    planner = FakeTripPlanner()
    await SydneyPages(pins=lambda: None, options=None, planner=planner).close()
    assert planner.closed


async def test_sydney_pages_close_without_a_planner() -> None:
    """Closing pages with no Trip Planner does nothing."""
    await SydneyPages(pins=lambda: None, options=None, planner=None).close()


@pytest.mark.parametrize(('name', 'current'), [
    ('live.html', '/'),
    ('settings.html', '/settings'),
    ('pins.html', '/pins'),
    ('pin-edit.html', '/pins'),
])
def test_sydney_nav_matches_board_templates(
    name: str, current: str
) -> None:
    """The app's own templates carry exactly the nav Sydney gives the kit."""
    text = (files('paperroute') / 'assets' / 'web' / name).read_text(
        encoding='utf-8',
    )
    found = re.search(r'<nav\b.*?</nav>', text, flags=re.DOTALL)
    assert found is not None
    assert found.group(0) == nav_html(items=SYDNEY_NAV, current=current)
