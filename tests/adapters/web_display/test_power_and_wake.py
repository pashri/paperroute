"""Tests for web display: power and wake."""

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from http import HTTPStatus
from typing import cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.adapters.web_live import live_page
from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import PowerAction
from paperroute_device_testkit.device_pages import DeviceSite
from paperroute_hardware.core.knob_events import Knob, KnobPressed
from paperroute_sydney.adapters.web_board import SYDNEY_HINT, SYDNEY_NAV
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.kit_steps import step
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.core.messages import Event
from paperroute_transit.core.saved import LiveView
from tests.adapters.transit_web import transit_display
from tests.adapters.web_display.helpers import POWER_ROUTES, Client, _client
from tests.core.test_quiet import asleep_at_one


async def test_power_post_posts_event(
    site: DeviceSite, client: Client
) -> None:
    """A known action posts PowerRequested; others and other sites fail."""
    good = await client.post('/power', json={'action': 'reboot'})
    halt = await client.post('/power', json={'action': 'halt'})
    cross = await client.post(
        '/power',
        json={'action': 'poweroff'},
        headers={'Origin': 'http://evil.example'},
    )
    form = await client.post(
        '/power', data={'action': 'poweroff'}, allow_redirects=False,
    )
    assert good.status == HTTPStatus.NO_CONTENT
    assert halt.status == HTTPStatus.BAD_REQUEST
    assert cross.status == HTTPStatus.FORBIDDEN
    assert form.status == HTTPStatus.SEE_OTHER
    assert form.headers['Location'] == '/device?power=poweroff'
    assert site.posted == [
        PowerRequested(action=PowerAction.RESTART),
        PowerRequested(action=PowerAction.OFF),
    ]


@pytest.mark.parametrize(('action', 'question', 'button'), [
    (
        'poweroff',
        "Shut down now? The screen will show OFF when it's safe to unplug.",
        'Shut down',
    ),
    ('reboot', 'Restart now?', 'Restart'),
])
async def test_power_works_without_script(
    *,
    site: DeviceSite,
    client: Client,
    action: str,
    question: str,
    button: str,
) -> None:
    """Button, confirm page, its form, then the page saying so."""
    confirm = await client.post(f'/device/power?action={action}')
    text = await confirm.text()
    assert f'<p>{question}</p>' in text
    assert f'name="action" value="{action}"' in text
    assert f'>{button}</button>' in text
    assert '<a class="button secondary" href="/device">Cancel</a>' in text
    assert '{{' not in text
    assert not site.posted
    done = await client.post(
        '/power', data={'action': action}, allow_redirects=False,
    )
    assert done.headers['Location'] == f'/device?power={action}'
    assert site.posted == [PowerRequested(action=PowerAction(action))]


async def test_power_page_rejects_unknown(client: Client) -> None:
    """The confirm page needs a known action."""
    response = await client.get('/device/power?action=halt')
    assert response.status == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize('path', ['/power', '/device/power'])
async def test_post_with_null_origin_is_refused(
    posted: list[object], client: Client, path: str,
) -> None:
    """A sandboxed page sends Origin: null; it can't reach the power routes."""
    response = await client.post(
        path,
        data={'action': 'poweroff'},
        headers={'Origin': 'null'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not posted


def test_page_hint_names_settings_chord() -> None:
    """The hint says how to open Settings from the page."""
    html = live_page(nav=SYDNEY_NAV, hint=SYDNEY_HINT)
    plain = re.sub(r'<[^>]+>', '', ' '.join(html.split()))
    assert (
        'Hold S and K together, or both Press buttons, for Settings.'
        in plain
    )


@pytest.mark.parametrize(('method', 'path'), POWER_ROUTES)
async def test_no_power_without_device_pages(method: str, path: str) -> None:
    """Without device pages, as in replay, power is not served."""
    client = await _client(display=transit_display(city=SYDNEY))
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.NOT_FOUND
    finally:
        await client.close()


async def test_web_wake_keeps_the_map_view(
    posted: list[object], client: Client
) -> None:
    """The live calls that woke the Pi wake it without leaving the Map."""
    turn = {'knob': 'top', 'action': 'turn', 'steps': 0}
    press = {'knob': 'bottom', 'action': 'press'}
    rejected = await client.post('/knob', json=turn)
    pressed = await client.post('/knob', json=press)
    state = replace(asleep_at_one(), view=LiveView.MAP)
    for event in posted:
        state, _ = step(state, event)
    assert rejected.status == HTTPStatus.BAD_REQUEST
    assert pressed.status == HTTPStatus.NO_CONTENT
    assert posted == [KnobPressed(knob=Knob.BOTTOM)]
    assert not state.panel.asleep
    assert state.view == LiveView.MAP


def test_settle_is_immediate(display: WebDisplay) -> None:
    """A browser copy is always settled."""
    assert display.settle(timeout=0)


class Board:
    """
    Board pages that serve nothing and count how often they close.

    Attributes
    ----------
    closes : int
        Times `close` was awaited.
    """

    def __init__(self) -> None:
        self.closes = 0

    def routes(
        self,
        *,
        send: Callable[[Event], None],
        reported: Callable[[], Mapping[str, str]],
    ) -> list[web.RouteDef]:
        """
        List no routes.

        Parameters
        ----------
        send : Callable[[Event], None]
            Ignored.
        reported : Callable[[], Mapping[str, str]]
            Ignored.

        Returns
        -------
        list[web.RouteDef]
            No routes.
        """
        del send, reported
        return []

    async def close(self) -> None:
        """Count the close."""
        self.closes += 1


async def test_board_pages_closed_on_shutdown() -> None:
    """The board pages are closed once when the app shuts down."""
    board = Board()
    client = await _client(
        display=transit_display(board=cast(PinPages, board), city=SYDNEY),
    )
    await client.close()
    assert board.closes == 1


async def test_knobs_reach_app_bound_after_app() -> None:
    """A sink bound after the app is built still gets knob posts."""
    display = transit_display(city=SYDNEY)
    client = TestClient(TestServer(display.app()))
    got: list[object] = []
    display.bind(post=got.append)
    await client.start_server()
    try:
        response = await client.post(
            '/knob', data={'knob': 'top', 'action': 'press'},
        )
    finally:
        await client.close()
    assert response.status == HTTPStatus.NO_CONTENT
    assert got == [KnobPressed(knob=Knob.TOP)]


async def test_display_without_device_serves_shell_files() -> None:
    """With no device pages the live page and shell files still serve."""
    client = await _client(display=transit_display(city=SYDNEY))
    try:
        statuses = {
            path: (await client.get(path)).status
            for path in (
                '/',
                '/live.css',
                '/fonts/public-sans.woff2',
                '/vendor/tom-select.css',
                '/device',
            )
        }
    finally:
        await client.close()
    assert statuses == {
        '/': HTTPStatus.OK,
        '/live.css': HTTPStatus.OK,
        '/fonts/public-sans.woff2': HTTPStatus.OK,
        '/vendor/tom-select.css': HTTPStatus.OK,
        '/device': HTTPStatus.NOT_FOUND,
    }
