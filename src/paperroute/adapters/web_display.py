"""Serve the frame and knob buttons to a browser on the local network."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

import numpy as np
from aiohttp import web
from numpy.typing import NDArray

from paperroute.adapters.settings_form import (
    ERRORS,
    BadInput,
    UnknownValue,
    changed_from,
    form_values,
    page_text,
    posted_values,
)
from paperroute.core.messages import Event, SettingsChanged
from paperroute.core.saved import Settings
from paperroute_device.adapters.web_common import (
    asset,
    asset_bytes,
    form_fields,
)
from paperroute_device.adapters.web_guard import (
    cross_site,
    is_local,
    known_host,
)
from paperroute_device.render.canvas import encode_png
from paperroute_device.rss import max_rss_mb
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_hardware.ports import CLEAN_AFTER

if TYPE_CHECKING:
    from paperroute.adapters.web_board import BoardPages
    from paperroute_device.adapters.web_device import DevicePages

logger = logging.getLogger(__name__)

PING_S: Final[float] = 15  # seconds of quiet before an SSE comment
ACTIONS: Final[dict[str, int]] = {'left': -1, 'right': 1}
VENDORED: Final[dict[str, str]] = {
    'tom-select.complete.min.js': 'text/javascript',
    'tom-select.css': 'text/css',
}
BOTH: Final[str] = 'both'  # the knob name for both knobs together
HELD_ACTIONS: Final[dict[str, int]] = {'hold-left': -1, 'hold-right': 1}

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def knob_event(*, knob: str, action: str) -> Event | None:
    """
    Turn a knob name and action from the page into an event.

    Parameters
    ----------
    knob : str
        `top`, `bottom`, or `both`, which only takes `hold`.
    action : str
        `left`, `right`, `press`, `hold`, or `hold-left` or `hold-right`
        for a turn while held.

    Returns
    -------
    Event | None
        The event, or None if either value is not recognised.
    """
    if knob == BOTH:
        return KnobsHeld() if action == 'hold' else None
    if knob not in {member.value for member in Knob}:
        return None
    return member_event(knob=Knob(knob), action=action)


def member_event(*, knob: Knob, action: str) -> Event | None:
    """
    Turn an action on one knob into an event.

    Parameters
    ----------
    knob : Knob
        The knob.
    action : str
        `left`, `right`, `press`, `hold`, `hold-left` or `hold-right`.

    Returns
    -------
    Event | None
        The event, or None if the action is not recognised.
    """
    if action == 'press':
        return KnobPressed(knob=knob)
    if action == 'hold':
        return KnobHeld(knob=knob)
    if action in HELD_ACTIONS:
        return KnobHeldTurned(knob=knob, steps=HELD_ACTIONS[action])
    if action in ACTIONS:
        return KnobTurned(knob=knob, steps=ACTIONS[action])
    return None


def status_settings(*, status: Mapping[str, object]) -> dict[str, str]:
    """
    Read the settings fields the app reported, over the defaults.

    Parameters
    ----------
    status : Mapping[str, object]
        The status page's extra fields.

    Returns
    -------
    dict[str, str]
        The form values; the defaults until the app reports.
    """
    values = form_values(settings=Settings())
    reported = status.get('settings')
    if isinstance(reported, Mapping):
        values.update({
            key: value
            for key, value in reported.items()
            if isinstance(value, str)
        })
    return values


async def pinged_wait(
    *, waiting: asyncio.Event, response: web.StreamResponse,
) -> bool:
    """
    Wait for an event, pinging the stream if it takes `PING_S`.

    Parameters
    ----------
    waiting : asyncio.Event
        The event waited for.
    response : web.StreamResponse
        The open event stream.

    Returns
    -------
    bool
        True when the event was set; False after a ping.
    """
    try:
        await asyncio.wait_for(waiting.wait(), PING_S)
    except TimeoutError:
        await response.write(b': ping\n\n')
        return False
    return True


def settings_event(
    *, fields: Mapping[str, str], as_json: bool,
) -> SettingsChanged | web.Response:
    """
    Read a posted settings form.

    Parameters
    ----------
    fields : Mapping[str, str]
        The posted fields.
    as_json : bool
        Whether the fields came from the script as JSON.

    Returns
    -------
    SettingsChanged | web.Response
        The change; a 400 response for an unknown value; for JSON, a 422
        response carrying the error text for a time that is not usable.

    Raises
    ------
    web.HTTPSeeOther
        For a form, to the page's error line for a time that is not
        usable.
    """
    try:
        return changed_from(fields=fields)
    except UnknownValue:
        return web.Response(status=HTTPStatus.BAD_REQUEST)
    except BadInput as error:
        if as_json:
            return web.json_response(
                {'error': ERRORS[error.section]},
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        raise web.HTTPSeeOther(
            location=f'/settings?error={error.section}',
        ) from error


def peer_address(request: web.BaseRequest) -> str | None:
    """
    Get the address of the connected peer.

    Parameters
    ----------
    request : web.BaseRequest
        The incoming request.

    Returns
    -------
    str | None
        The peer's IP address, or None if it is unknown.
    """
    return request.remote


class WebDisplay:
    """
    A display that serves the latest frame over HTTP.

    Only private and loopback peers are served. Browsers follow new
    frames over server-sent events and send knob presses back as POSTs.

    Parameters
    ----------
    status : Callable[[], dict[str, object]] | None
        Extra fields for the status page.
    remote_of : Callable[[web.BaseRequest], str | None]
        Finds the peer address of a request.
    device : DevicePages | None
        The device's own pages, such as Wi-Fi setup, or None.
    board : BoardPages | None
        The board's own pages, such as pins, or None.

    Attributes
    ----------
    device : DevicePages | None
        The device's own pages; set before `app` is called.
    boot : str
        Hex start time of this server, so a restart changes event data.
    version : int
        Number of frames shown; 0 before the first.
    changed : asyncio.Event
        Set, then replaced, each time a frame is shown.
    """

    def __init__(
        self,
        *,
        status: Callable[[], dict[str, object]] | None = None,
        remote_of: Callable[[web.BaseRequest], str | None] = peer_address,
        device: 'DevicePages | None' = None,
        board: 'BoardPages | None' = None,
    ) -> None:
        self._status = status
        self.device = device
        self._board = board
        self._remote_of = remote_of
        self._post: Callable[[Event], None] = lambda event: None
        self._posted: dict[str, str] = {}
        self._before: dict[str, str] = {}
        self._png: bytes | None = None
        self._started = time.monotonic()
        self._closing = False
        self.boot = f'{time.time_ns():x}'
        self.version = 0
        self.changed = asyncio.Event()

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
    ) -> None:
        """
        Store a frame and wake the event streams.

        The copy is always kept the right way up.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels.
        full : bool
            Ignored.
        turned : bool
            Ignored.
        quick : bool
            Ignored.
        asleep : bool
            Ignored.
        clear : bool
            Ignored.
        clean_after : int
            Ignored.
        """
        del full, turned, quick, asleep, clear, clean_after
        self._png = encode_png(grey=frame)
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()

    def settle(self, *, timeout: float) -> bool:
        """
        Report that every frame is shown, which it always is.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            Always True.
        """
        del timeout
        return True

    def bind(self, *, post: Callable[[Event], None]) -> None:
        """
        Set where knob events from the browser go.

        Parameters
        ----------
        post : Callable[[Event], None]
            Called with each knob event.
        """
        self._post = post

    def send(self, event: Event) -> None:
        """
        Send an event to wherever `bind` last pointed.

        Parameters
        ----------
        event : Event
            The event.
        """
        self._post(event)

    def app(self) -> web.Application:
        """
        Build the web application.

        Returns
        -------
        web.Application
            The routes, behind the local-network check. The board's
            pages are served only when there are board pages; power and
            the device's own pages only when there are device pages.
        """
        app = web.Application(middlewares=[self._guard])
        app.on_shutdown.append(self._close)
        app.add_routes([
            web.get('/', self._page),
            web.get('/live.css', self._css),
            web.get('/fonts/public-sans.woff2', self._font),
            web.get('/vendor/{name}', self._vendor),
            web.get('/live.js', self._js),
            web.get('/frame.png', self._frame),
            web.get('/events', self._events),
            web.post('/knob', self._knob),
            web.get('/settings', self._settings_page),
            web.get('/settings.js', self._settings_js),
            web.post('/settings', self._settings),
            web.get('/status', self._status_json),
        ])
        if self._board is not None:
            app.add_routes(self._board.routes(
                send=self.send, reported=self._reported,
            ))
        if self.device is not None:
            app.add_routes(self.device.routes())
        return app

    async def _close(self, app: web.Application) -> None:
        """
        Release the open event streams and close the board's pages.

        Parameters
        ----------
        app : web.Application
            The application being shut down.
        """
        del app
        self._closing = True
        self.changed.set()
        if self._board is not None:
            await self._board.close()

    @web.middleware
    async def _guard(
        self, request: web.Request, handler: Handler
    ) -> web.StreamResponse:
        """
        Refuse peers outside the local network and cross-site POSTs.

        Parameters
        ----------
        request : web.Request
            The incoming request.
        handler : Handler
            The route handler.

        Returns
        -------
        web.StreamResponse
            A 403 response, or the handler's response.
        """
        if not is_local(remote=self._remote_of(request)):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        if not known_host(request=request):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        if request.method == 'POST' and cross_site(request=request):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        return await handler(request)

    async def _page(self, request: web.Request) -> web.Response:
        """
        Serve the page.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        return web.Response(
            text=asset(name='live.html'),
            content_type='text/html',
        )

    async def _css(self, request: web.Request) -> web.Response:
        """
        Serve the stylesheet.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        return web.Response(
            text=asset(name='live.css'),
            content_type='text/css',
        )

    async def _font(self, request: web.Request) -> web.Response:
        """
        Serve the page font.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The woff2 file, cached for a day.
        """
        del request
        return web.Response(
            body=asset_bytes(name='fonts/public-sans.woff2'),
            content_type='font/woff2',
            headers={'Cache-Control': 'max-age=86400'},
        )

    async def _vendor(self, request: web.Request) -> web.Response:
        """
        Serve a vendored script or stylesheet.

        Parameters
        ----------
        request : web.Request
            The incoming request, with the file name in its path.

        Returns
        -------
        web.Response
            The file, cached for a day.

        Raises
        ------
        web.HTTPNotFound
            If the name is not a vendored file.
        """
        name = request.match_info['name']
        if name not in VENDORED:
            raise web.HTTPNotFound
        return web.Response(
            body=asset_bytes(name=f'vendor/{name}'),
            content_type=VENDORED[name],
            headers={'Cache-Control': 'max-age=86400'},
        )

    async def _js(self, request: web.Request) -> web.Response:
        """
        Serve the script.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        return web.Response(
            text=asset(name='live.js'),
            content_type='text/javascript',
        )

    async def _frame(self, request: web.Request) -> web.Response:
        """
        Serve the latest frame as a PNG.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        if self._png is None:
            return web.Response(status=HTTPStatus.SERVICE_UNAVAILABLE)
        return web.Response(
            body=self._png,
            content_type='image/png',
            headers={'Cache-Control': 'no-store'},
        )

    async def _events(self, request: web.Request) -> web.StreamResponse:
        """
        Stream frame versions as server-sent events.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.StreamResponse
            The response.
        """
        response = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-store',
        })
        await response.prepare(request)
        try:
            await self._stream(response=response)
        except ConnectionResetError:
            logger.debug('event stream closed by the browser')
        return response

    async def _stream(self, *, response: web.StreamResponse) -> None:
        """
        Send each new frame version, pinging while none comes, until close.

        Parameters
        ----------
        response : web.StreamResponse
            The open event stream.
        """
        waiting = self.changed
        if self.version:
            await self._send_version(response=response)
        while True:
            if not await pinged_wait(waiting=waiting, response=response):
                continue
            if self._closing:
                return
            waiting = self.changed
            await self._send_version(response=response)

    async def _send_version(self, *, response: web.StreamResponse) -> None:
        """
        Write the current frame version as a `frame` event.

        Parameters
        ----------
        response : web.StreamResponse
            The open event stream.
        """
        message = f'event: frame\ndata: {self.boot}-{self.version}\n\n'
        await response.write(message.encode())

    async def _knob(self, request: web.Request) -> web.Response:
        """
        Post a knob event from the browser.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        fields = await form_fields(request=request)
        event = knob_event(
            knob=fields.get('knob', ''),
            action=fields.get('action', ''),
        )
        if event is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        self._post(event)
        return web.Response(status=HTTPStatus.NO_CONTENT)

    def _reported(self) -> dict[str, str]:
        """
        Read the settings the app last reported, over the defaults.

        Returns
        -------
        dict[str, str]
            The form values.
        """
        return status_settings(status=self._status() if self._status else {})

    async def _settings_page(self, request: web.Request) -> web.Response:
        """
        Serve the Settings page, drawn as just saved or else as reported.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        status = self._reported()
        if self._posted.items() <= status.items() or status != self._before:
            self._posted = {}
        page = page_text(
            template=asset(name='settings.html'),
            values={**status, **self._posted},
            error=request.query.get('error'),
        )
        return web.Response(text=page, content_type='text/html')

    async def _settings_js(self, request: web.Request) -> web.Response:
        """
        Serve the Settings page's script.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        return web.Response(
            text=asset(name='settings.js'),
            content_type='text/javascript',
        )

    async def _settings(self, request: web.Request) -> web.Response:
        """
        Post a changed setting from the browser.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            400 for an unknown value. For a time that is not usable, 422
            with the error text to the script's JSON, or 303 to the
            page's error line for a plain form. Otherwise 204 to the
            script's JSON, or 303 back to the page for a plain form.
        """
        fields = await form_fields(request=request)
        as_json = request.content_type == 'application/json'
        event = settings_event(fields=fields, as_json=as_json)
        if isinstance(event, web.Response):
            return event
        self._before = self._reported()
        self._posted = posted_values(fields=fields, event=event)
        self._post(event)
        if as_json:
            return web.Response(status=HTTPStatus.NO_CONTENT)
        raise web.HTTPSeeOther(location='/settings')

    async def _status_json(self, request: web.Request) -> web.Response:
        """
        Serve the status as JSON.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The response.
        """
        del request
        extra = self._status() if self._status else {}
        return web.json_response({
            **extra,
            'version': self.version,
            'uptime_s': time.monotonic() - self._started,
            'max_rss_mb': max_rss_mb(),
        })
