"""Serve the frame and knob buttons to a browser on the local network."""

import asyncio
import ipaddress
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from importlib.resources import files
from typing import Final
from urllib.parse import urlsplit

import numpy as np
from aiohttp import web
from numpy.typing import NDArray

from paperroute.core.state import Event, Knob, KnobPressed, KnobTurned
from paperroute.observers import max_rss_mb
from paperroute.render.canvas import encode_png

logger = logging.getLogger(__name__)

PING_S: Final[float] = 15  # seconds of quiet before an SSE comment
ACTIONS: Final[dict[str, int]] = {'left': -1, 'right': 1}

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def knob_event(*, knob: str, action: str) -> Event | None:
    """
    Turn a knob name and action from the page into an event.

    Parameters
    ----------
    knob : str
        `top` or `bottom`.
    action : str
        `left`, `right` or `press`.

    Returns
    -------
    Event | None
        The event, or None if either value is not recognised.
    """
    if knob not in {member.value for member in Knob}:
        return None
    if action == 'press':
        return KnobPressed(knob=Knob(knob))
    if action in ACTIONS:
        return KnobTurned(knob=Knob(knob), steps=ACTIONS[action])
    return None


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


def _is_local(*, remote: str | None) -> bool:
    """
    Check whether an address is private or loopback.

    Parameters
    ----------
    remote : str | None
        An IP address in text form.

    Returns
    -------
    bool
        False for a missing or unparsable address.
    """
    try:
        address = ipaddress.ip_address(remote or '')
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def _cross_site(*, request: web.Request) -> bool:
    """
    Check whether a POST names a different origin from its own host.

    Parameters
    ----------
    request : web.Request
        The incoming request.

    Returns
    -------
    bool
        True if an `Origin` header is present and its host differs
        from the `Host` header.
    """
    origin = request.headers.get('Origin')
    if origin is None:
        return False
    return urlsplit(origin).netloc != request.host


def _asset(*, name: str) -> str:
    """
    Read one of the packaged page files.

    Parameters
    ----------
    name : str
        File name within `assets/web`.

    Returns
    -------
    str
        The file contents.
    """
    path = files('paperroute') / 'assets' / 'web' / name
    return path.read_text(encoding='utf-8')


async def _fields(*, request: web.Request) -> dict[str, str]:
    """
    Read the knob and action from a form or JSON body.

    Parameters
    ----------
    request : web.Request
        The incoming request.

    Returns
    -------
    dict[str, str]
        The string fields found; empty if the body is not readable.
    """
    if request.content_type == 'application/json':
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return {}
    else:
        body = await request.post()
    if not isinstance(body, Mapping):
        return {}
    return {k: v for k, v in body.items() if isinstance(v, str)}


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

    Attributes
    ----------
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
    ) -> None:
        self._status = status
        self._remote_of = remote_of
        self._post: Callable[[Event], None] = lambda event: None
        self._png: bytes | None = None
        self._started = time.monotonic()
        self.version = 0
        self.changed = asyncio.Event()

    def show(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Store a frame and wake the event streams.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels.
        """
        self._png = encode_png(grey=frame)
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()

    def bind(self, *, post: Callable[[Event], None]) -> None:
        """
        Set where knob events from the browser go.

        Parameters
        ----------
        post : Callable[[Event], None]
            Called with each knob event.
        """
        self._post = post

    def app(self) -> web.Application:
        """
        Build the web application.

        Returns
        -------
        web.Application
            The routes, behind the local-network check.
        """
        app = web.Application(middlewares=[self._guard])
        app.add_routes([
            web.get('/', self._page),
            web.get('/live.css', self._css),
            web.get('/live.js', self._js),
            web.get('/frame.png', self._frame),
            web.get('/events', self._events),
            web.post('/knob', self._knob),
            web.get('/status', self._status_json),
        ])
        return app

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
        if not _is_local(remote=self._remote_of(request)):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        if request.method == 'POST' and _cross_site(request=request):
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
            text=_asset(name='live.html'),
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
            text=_asset(name='live.css'),
            content_type='text/css',
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
            text=_asset(name='live.js'),
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
        waiting = self.changed
        try:
            if self.version:
                await self._send_version(response=response)
            while True:
                try:
                    await asyncio.wait_for(waiting.wait(), PING_S)
                except TimeoutError:
                    await response.write(b': ping\n\n')
                    continue
                waiting = self.changed
                await self._send_version(response=response)
        except ConnectionResetError:
            logger.debug('event stream closed by the browser')
        return response

    async def _send_version(self, *, response: web.StreamResponse) -> None:
        """
        Write the current frame version as a `frame` event.

        Parameters
        ----------
        response : web.StreamResponse
            The open event stream.
        """
        message = f'event: frame\ndata: {self.version}\n\n'
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
        fields = await _fields(request=request)
        event = knob_event(
            knob=fields.get('knob', ''),
            action=fields.get('action', ''),
        )
        if event is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        self._post(event)
        return web.Response(status=HTTPStatus.NO_CONTENT)

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
            'version': self.version,
            'uptime_s': time.monotonic() - self._started,
            'max_rss_mb': max_rss_mb(),
            **extra,
        })
