"""Tests for the kit's web display."""

import asyncio
import time
from collections.abc import AsyncIterator
from http import HTTPStatus

import numpy as np
import pytest
from aiohttp import ClientResponse, web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_device.adapters import web_live
from paperroute_device.adapters.settings_page import SettingsPage
from paperroute_device.adapters.web_display import (
    KIT_PATHS,
    KitRoutes,
    WebDisplay,
    kit_shadowed,
)
from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_device.adapters.web_live import knob_event
from paperroute_device.core.device_events import PowerRequested
from paperroute_device.core.device_model import PowerAction
from paperroute_device.render.canvas import encode_png
from paperroute_device_testkit.device_pages import DeviceSite
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from tests.core.fake_app import kit_step
from tests.core.test_reducer import sleeping

Client = TestClient[web.Request, web.Application]
FRAME = np.full((480, 800), 255, dtype=np.uint8)
POWER_ROUTES = [
    ('GET', '/device/power'),
    ('POST', '/device/power'),
    ('POST', '/power'),
]
ROUTES = [
    ('GET', '/'),
    ('GET', '/live.css'),
    ('GET', '/live.js'),
    ('GET', '/frame.png'),
    ('GET', '/events'),
    ('GET', '/status'),
    ('POST', '/knob'),
    ('GET', '/settings'),
    ('GET', '/settings.js'),
    ('POST', '/settings'),
    *POWER_ROUTES,
]


def settings_page() -> SettingsPage:
    """
    Make a Settings page with the device's section alone.

    Returns
    -------
    SettingsPage
        The page, posting nowhere.
    """
    return SettingsPage(
        status=dict, post=lambda event: None, plug=None, quiet_label=None,
    )


async def _client(*, display: WebDisplay) -> Client:
    """Start a client on a free local port."""
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


@pytest.fixture(name='site')
def _site() -> DeviceSite:
    """The kit's device pages, recording what they post."""
    return DeviceSite()


@pytest.fixture(name='display')
def _display(site: DeviceSite) -> WebDisplay:
    """A display with device and Settings pages that has shown nothing."""
    return WebDisplay(device=site.pages(), settings=settings_page())


@pytest.fixture(name='client')
async def _web_client(display: WebDisplay) -> AsyncIterator[Client]:
    """A client of the display's app."""
    client = await _client(display=display)
    yield client
    await client.close()


async def _read_event(*, response: ClientResponse) -> tuple[str, str]:
    """Read one server-sent event as (name, data)."""
    async with asyncio.timeout(2):
        name = (await response.content.readline()).decode().strip()
        data = (await response.content.readline()).decode().strip()
        await response.content.readline()
    return name.removeprefix('event: '), data.removeprefix('data: ')


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


@pytest.mark.parametrize(('knob', 'action', 'expected'), [
    ('top', 'left', KnobTurned(knob=Knob.TOP, steps=-1)),
    ('top', 'right', KnobTurned(knob=Knob.TOP, steps=1)),
    ('top', 'press', KnobPressed(knob=Knob.TOP)),
    ('bottom', 'left', KnobTurned(knob=Knob.BOTTOM, steps=-1)),
    ('bottom', 'right', KnobTurned(knob=Knob.BOTTOM, steps=1)),
    ('bottom', 'press', KnobPressed(knob=Knob.BOTTOM)),
    ('bottom', 'hold', KnobHeld(knob=Knob.BOTTOM)),
    ('both', 'hold', KnobsHeld()),
    ('both', 'press', None),
    ('middle', 'left', None),
    ('top', 'spin', None),
])
def test_knob_event_mapping(
    knob: str, action: str, expected: object | None
) -> None:
    """Valid knob and action pairs map to events, others to None."""
    assert knob_event(knob=knob, action=action) == expected


async def test_frame_png_503_before_first_frame(
    display: WebDisplay, client: Client
) -> None:
    """There is no frame until the first show."""
    response = await client.get('/frame.png')
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    display.show(frame=FRAME)
    response = await client.get('/frame.png')
    assert response.status == HTTPStatus.OK
    assert response.content_type == 'image/png'
    assert response.headers['Cache-Control'] == 'no-store'
    assert (await response.read()).startswith(b'\x89PNG')


async def test_web_copy_stays_upright(
    display: WebDisplay, client: Client
) -> None:
    """A turned frame is served as drawn, the right way up."""
    frame = FRAME.copy()
    frame[0, 0] = 0
    display.show(frame=frame, full=True, turned=True)
    response = await client.get('/frame.png')
    assert await response.read() == encode_png(grey=frame)


async def test_events_sends_current_frame_version_on_connect(
    display: WebDisplay, client: Client
) -> None:
    """A new stream starts with the version already shown."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    assert response.content_type == 'text/event-stream'
    assert response.headers['Cache-Control'] == 'no-store'
    assert await _read_event(response=response) == (
        'frame', f'{display.live.boot}-1',
    )


async def test_events_pushes_new_versions(
    display: WebDisplay, client: Client
) -> None:
    """Each new frame is pushed to an open stream."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    boot = display.live.boot
    assert await _read_event(response=response) == ('frame', f'{boot}-1')
    display.show(frame=FRAME)
    assert await _read_event(response=response) == ('frame', f'{boot}-2')
    display.show(frame=FRAME)
    assert await _read_event(response=response) == ('frame', f'{boot}-3')


async def test_events_pings_when_idle(
    display: WebDisplay,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quiet stream gets a comment line to keep it open."""
    monkeypatch.setattr(web_live, 'PING_S', 0.05)
    response = await client.get('/events')
    async with asyncio.timeout(2):
        line = await response.content.readline()
    assert line.decode().strip() == ': ping'
    assert display.live.version == 0


async def test_knob_post_posts_event(
    display: WebDisplay, client: Client
) -> None:
    """A knob POST becomes an event on the bound queue."""
    posted: list[object] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/knob',
        data={'knob': 'bottom', 'action': 'right'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    response = await client.post(
        '/knob',
        json={'knob': 'top', 'action': 'press'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    assert posted == [
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        KnobPressed(knob=Knob.TOP),
    ]


async def test_knob_both_hold_posts_knobs_held(
    display: WebDisplay, client: Client
) -> None:
    """Both-knob hold posts KnobsHeld; any other both action is a 400."""
    posted: list[object] = []
    display.bind(post=posted.append)
    response = await client.post(
        '/knob', data={'knob': 'both', 'action': 'hold'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    response = await client.post(
        '/knob', data={'knob': 'both', 'action': 'press'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert posted == [KnobsHeld()]


@pytest.mark.parametrize('payload', [
    {'knob': 'middle', 'action': 'left'},
    {'knob': 'top'},
])
async def test_knob_post_rejects_unknown(
    client: Client, payload: dict[str, str]
) -> None:
    """Unknown values are a 400."""
    response = await client.post('/knob', data=payload)
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_knob_post_rejects_bad_json(client: Client) -> None:
    """A JSON body that is not an object is a 400."""
    response = await client.post('/knob', json=['top', 'left'])
    assert response.status == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize(('method', 'path'), ROUTES)
@pytest.mark.parametrize('remote', [
    '8.8.8.8',
    None,
    'not-an-ip',
    '::ffff:8.8.8.8',
    '2001:4860::8888',
])
async def test_public_address_is_refused_on_every_route(
    method: str, path: str, remote: str | None
) -> None:
    """Only private and loopback peers are served."""
    display = WebDisplay(
        remote_of=lambda request: remote,
        device=DeviceSite().pages(),
        settings=settings_page(),
    )
    client = await _client(display=display)
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.FORBIDDEN
    finally:
        await client.close()


@pytest.mark.parametrize('remote', [
    '192.168.0.19',
    '127.0.0.1',
    '::1',
    'fe80::1',
    'fd00::1',
])
async def test_private_address_is_allowed(remote: str) -> None:
    """Private and loopback peers are served."""
    display = WebDisplay(
        remote_of=lambda request: remote,
        device=DeviceSite().pages(),
        settings=settings_page(),
    )
    client = await _client(display=display)
    try:
        response = await client.get('/status')
        assert response.status == HTTPStatus.OK
    finally:
        await client.close()


async def test_knob_cross_site_origin_is_refused(
    client: Client,
) -> None:
    """A POST from another site is refused; the same site is fine."""
    data = {'knob': 'top', 'action': 'press'}
    host = f'{client.host}:{client.port}'
    bad = await client.post(
        '/knob',
        data=data,
        headers={'Origin': 'http://evil.example'},
    )
    good = await client.post(
        '/knob',
        data=data,
        headers={'Origin': f'http://{host}'},
    )
    none = await client.post('/knob', data=data)
    assert bad.status == HTTPStatus.FORBIDDEN
    assert good.status == HTTPStatus.NO_CONTENT
    assert none.status == HTTPStatus.NO_CONTENT


async def test_status_reports_version_and_rss() -> None:
    """The status JSON has the version, memory and the status dict."""
    display = WebDisplay(status=lambda: {'frames': 7})
    client = await _client(display=display)
    try:
        display.show(frame=FRAME)
        body = await (await client.get('/status')).json()
    finally:
        await client.close()
    assert body['version'] == 1
    assert body['frames'] == 7
    assert body['max_rss_mb'] > 0
    assert body['uptime_s'] >= 0


async def test_page_is_served(client: Client) -> None:
    """The page and its assets are served."""
    page = await client.get('/')
    assert page.status == HTTPStatus.OK
    assert page.content_type == 'text/html'
    css = await client.get('/live.css')
    assert css.content_type == 'text/css'
    script = await client.get('/live.js')
    assert script.content_type in {'text/javascript', 'application/javascript'}


def test_page_assets_are_packaged() -> None:
    """The three web files ship with the package."""
    html = (SHELL_WEB / 'live.html').read_text(encoding='utf-8')
    assert (SHELL_WEB / 'live.css').is_file()
    assert (SHELL_WEB / 'live.js').is_file()
    assert 'live.css' in html
    assert 'live.js' in html
    assert 'role="status"' not in html


async def test_event_data_carries_a_boot_id(
    display: WebDisplay, client: Client
) -> None:
    """The boot id is hex, so a restart changes the image URL."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    _, data = await _read_event(response=response)
    boot, version = data.split('-')
    assert int(boot, 16) >= 0
    assert boot == display.live.boot
    assert version == '1'


@pytest.mark.parametrize('host', [
    'paperroute.local',
    'paperroute.local:80',
    'localhost',
    '192.168.0.94',
    '192.168.0.94:8080',
    '[fe80::1]',
    '[fe80::1]:8080',
])
async def test_known_host_header_is_allowed(
    client: Client, host: str
) -> None:
    """Names the device answers to are served."""
    response = await client.get('/status', headers={'Host': host})
    assert response.status == HTTPStatus.OK


@pytest.mark.parametrize('host', ['evil.example', 'evil.example:80'])
async def test_unknown_host_header_is_refused(
    client: Client, host: str
) -> None:
    """A rebound DNS name from a private peer is refused."""
    response = await client.get('/status', headers={'Host': host})
    assert response.status == HTTPStatus.FORBIDDEN


async def test_knob_post_rejects_invalid_utf8_json(
    client: Client,
) -> None:
    """A JSON body that is not valid UTF-8 is a 400, not a 500."""
    response = await client.post(
        '/knob',
        data=b'{"knob": "\xff"}',
        headers={'Content-Type': 'application/json'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_status_carries_line() -> None:
    """The status JSON carries the app's line, and none without one."""
    lined = WebDisplay(
        status=lambda: {'frames': 7},
        status_line=lambda *, status: f'{status["frames"]} frames',
    )
    bodies = []
    for display in (lined, WebDisplay(status=dict)):
        client = await _client(display=display)
        try:
            bodies.append(await (await client.get('/status')).json())
        finally:
            await client.close()
    assert bodies[0]['line'] == '7 frames'
    assert bodies[0]['frames'] == 7
    assert 'line' not in bodies[1]


async def test_status_built_in_keys_win() -> None:
    """Extras cannot overwrite the built-in status keys."""
    display = WebDisplay(
        status=lambda: {'version': 99, 'uptime_s': 'x'},
    )
    client = await _client(display=display)
    try:
        body = await (await client.get('/status')).json()
    finally:
        await client.close()
    assert body['version'] == 0
    assert isinstance(body['uptime_s'], float)


async def test_shutdown_closes_open_event_streams(
    display: WebDisplay,
) -> None:
    """An open stream does not hold up server shutdown."""
    client = await _client(display=display)
    response = await client.get('/events')
    display.show(frame=FRAME)
    await _read_event(response=response)
    started = time.monotonic()
    await client.server.close()
    elapsed = time.monotonic() - started
    await client.close()
    assert elapsed < 1


@pytest.mark.parametrize('host', ['[', '[::1', 'a]b'])
async def test_malformed_host_header_is_refused(
    client: Client, host: str
) -> None:
    """A Host header that cannot be parsed is a 403, not a 500."""
    response = await client.get('/status', headers={'Host': host})
    assert response.status == HTTPStatus.FORBIDDEN


def test_hold_action_posts_knob_held() -> None:
    """The hold action on the bottom knob becomes a KnobHeld event."""
    event = knob_event(knob='bottom', action='hold')
    assert event == KnobHeld(knob=Knob.BOTTOM)


@pytest.mark.parametrize(('action', 'steps'), [
    ('hold-left', -1),
    ('hold-right', 1),
])
def test_knob_event_held_turns(action: str, steps: int) -> None:
    """The hold-left and hold-right actions are turns while held."""
    assert knob_event(knob='bottom', action=action) == KnobHeldTurned(
        knob=Knob.BOTTOM, steps=steps,
    )


def test_page_click_handler_constants() -> None:
    """The script has a click listener, a zero-detail check and the timings."""
    script = (SHELL_WEB / 'live.js').read_text(encoding='utf-8')
    assert "addEventListener('click'" in script
    assert 'event.detail === 0' in script
    assert 'HOLD_MS = 800' in script
    assert 'DOUBLE_MS = 300' in script


@pytest.mark.parametrize(('action', 'question', 'button'), [
    (
        'poweroff',
        "Shut down now? The screen will show OFF when it's safe to unplug.",
        'Shut down',
    ),
    ('reboot', 'Restart now?', 'Restart'),
])
async def test_power_works_without_script(
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
    display: WebDisplay, client: Client, path: str,
) -> None:
    """A sandboxed page sends Origin: null; it can't reach the power routes."""
    posted: list[object] = []
    display.bind(post=posted.append)
    response = await client.post(
        path,
        data={'action': 'poweroff'},
        headers={'Origin': 'null'},
        allow_redirects=False,
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert not posted


@pytest.mark.parametrize(('method', 'path'), POWER_ROUTES)
async def test_no_power_without_device_pages(method: str, path: str) -> None:
    """Without device pages, as in replay, power is not served."""
    client = await _client(display=WebDisplay())
    try:
        response = await client.request(method, path)
        assert response.status == HTTPStatus.NOT_FOUND
    finally:
        await client.close()


def test_settle_is_immediate(display: WebDisplay) -> None:
    """A browser copy is always settled."""
    assert display.settle(timeout=0)


async def test_knobs_reach_app_bound_after_app() -> None:
    """A sink bound after the app is built still gets knob posts."""
    display = WebDisplay()
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
    client = await _client(display=WebDisplay())
    try:
        statuses = {
            path: (await client.get(path)).status
            for path in (
                '/',
                '/live.css',
                '/fonts/public-sans.woff2',
                '/device',
            )
        }
    finally:
        await client.close()
    assert statuses == {
        '/': HTTPStatus.OK,
        '/live.css': HTTPStatus.OK,
        '/fonts/public-sans.woff2': HTTPStatus.OK,
        '/device': HTTPStatus.NOT_FOUND,
    }


async def test_web_wake_only_wakes(
    display: WebDisplay, client: Client,
) -> None:
    """A press from the page while asleep only wakes the panel."""
    posted: list[object] = []
    display.bind(post=posted.append)
    pressed = await client.post(
        '/knob', json={'knob': 'bottom', 'action': 'press'},
    )
    kit = sleeping()
    for event in posted:
        kit, _ = kit_step(kit=kit, event=event)
    assert pressed.status == HTTPStatus.NO_CONTENT
    assert posted == [KnobPressed(knob=Knob.BOTTOM)]
    assert not kit.device.panel.asleep
    assert not kit.app.knobs


async def noop(request: web.Request) -> web.Response:
    """
    Answer with nothing.

    Parameters
    ----------
    request : web.Request
        Ignored.

    Returns
    -------
    web.Response
        An empty 204.
    """
    del request
    return web.Response(status=HTTPStatus.NO_CONTENT)


class Pages:
    """
    App pages that place the kit's routes as told and count closes.

    Parameters
    ----------
    order : str
        Where the kit's routes go: `around` puts the sheets first and
        Settings after `/pins`, `drop` leaves Settings out, `twice`
        repeats the sheets.
    extra : tuple[str, ...]
        More app paths, placed last.

    Attributes
    ----------
    closes : int
        Times `close` was awaited.
    """

    def __init__(
        self, *, order: str = 'around', extra: tuple[str, ...] = (),
    ) -> None:
        self.order = order
        self.extra = extra
        self.closes = 0

    def routes(self, *, kit: KitRoutes) -> list[web.RouteDef]:
        """
        Place the kit's routes among the app's.

        Parameters
        ----------
        kit : KitRoutes
            The kit's routes.

        Returns
        -------
        list[web.RouteDef]
            `/app.css`, the sheets, `/pins`, Settings, then the extras,
            as `order` bends it.
        """
        settings = [] if self.order == 'drop' else kit.settings
        sheets = kit.sheets * (2 if self.order == 'twice' else 1)
        return [
            web.get('/app.css', noop),
            *sheets,
            web.get('/pins', noop),
            *settings,
            *(web.get(path, noop) for path in self.extra),
        ]

    async def close(self) -> None:
        """Count the close."""
        self.closes += 1


def paths(*, display: WebDisplay) -> list[str]:
    """
    List the display's kit and app route paths with their methods.

    Parameters
    ----------
    display : WebDisplay
        The display.

    Returns
    -------
    list[str]
        `METHOD path` for each route, in order.
    """
    return [f'{route.method} {route.path}' for route in display.routes()]


def test_app_routes_keep_their_order() -> None:
    """The app's list is kept as placed; with none, sheets then Settings."""
    placed = WebDisplay(
        routes=Pages(), sheets=('palette.css',), settings=settings_page(),
    )
    bare = WebDisplay(sheets=('palette.css',), settings=settings_page())
    assert paths(display=placed) == [
        'GET /app.css',
        'GET /palette.css',
        'GET /pins',
        'GET /settings',
        'GET /settings.js',
        'POST /settings',
    ]
    assert paths(display=bare) == [
        'GET /palette.css',
        'GET /settings',
        'GET /settings.js',
        'POST /settings',
    ]


@pytest.mark.parametrize('order', ['drop', 'twice'])
def test_app_routes_must_place_every_kit_route(order: str) -> None:
    """A list that drops or repeats a kit route is refused."""
    display = WebDisplay(
        routes=Pages(order=order),
        sheets=('palette.css',),
        settings=settings_page(),
    )
    with pytest.raises(ValueError, match='kit route'):
        display.routes()


@pytest.mark.parametrize('path', [
    '/device/{x}',
    '/{name}',
    '/device/extra',
    '/status',
    '/fonts/{name}',
    '/setup.js',
])
def test_app_route_shadowing_a_kit_path_is_refused(path: str) -> None:
    """An app route on a kit path or a catch-all segment is refused."""
    display = WebDisplay(routes=Pages(extra=(path,)), settings=settings_page())
    with pytest.raises(ValueError, match='shadows a kit path'):
        display.app()


@pytest.mark.parametrize(('path', 'shadowed'), [
    *((path, True) for path in KIT_PATHS),
    ('/device/power', True),
    ('/{name}/x', True),
    ('/pins', False),
    ('/vendor/{name}', False),
    ('/pins/{index}', False),
    ('/devices', False),
])
def test_kit_shadowed(path: str, shadowed: bool) -> None:
    """Kit paths, paths under `/device/` and root catch-alls are the kit's."""
    assert kit_shadowed(path) is shadowed


async def test_app_pages_closed_on_shutdown() -> None:
    """The app's pages are closed once when the server shuts down."""
    pages = Pages()
    client = await _client(display=WebDisplay(routes=pages))
    await client.close()
    assert pages.closes == 1


async def test_app_pages_are_served(client: Client) -> None:
    """The app's own pages are served behind the kit's guard."""
    display = WebDisplay(routes=Pages(), settings=settings_page())
    served = await _client(display=display)
    try:
        pins = await served.get('/pins')
        settings = await served.get('/settings')
    finally:
        await served.close()
    assert pins.status == HTTPStatus.NO_CONTENT
    assert settings.status == HTTPStatus.OK
    assert (await client.get('/pins')).status == HTTPStatus.NOT_FOUND


async def test_no_settings_without_a_settings_page() -> None:
    """Without a Settings page none of its routes are served."""
    client = await _client(display=WebDisplay())
    try:
        statuses = [
            (await client.get('/settings')).status,
            (await client.get('/settings.js')).status,
        ]
    finally:
        await client.close()
    assert statuses == [HTTPStatus.NOT_FOUND, HTTPStatus.NOT_FOUND]
