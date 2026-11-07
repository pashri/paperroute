"""The live view: the latest frame and the knob buttons, in a browser."""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Final

import numpy as np
from aiohttp import web
from numpy.typing import NDArray

from paperroute_device.adapters.web_common import form_fields
from paperroute_device.adapters.web_frame import (
    KIT_NAV,
    NavItem,
    page_frame,
    shell_text,
)
from paperroute_device.render.canvas import encode_png
from paperroute_device.rss import max_rss_mb
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobInput,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)

logger = logging.getLogger(__name__)

PING_S: Final[float] = 15  # seconds of quiet before an SSE comment
ACTIONS: Final[dict[str, int]] = {'left': -1, 'right': 1}
BOTH: Final[str] = 'both'  # the knob name for both knobs together
HELD_ACTIONS: Final[dict[str, int]] = {'hold-left': -1, 'hold-right': 1}
HINT_MARK: Final[str] = '{{hint}}'
KNOB_HINT: Final[str] = '\n'.join((
    '        Keys: <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd> for the top knob,',
    '        <kbd>J</kbd> <kbd>K</kbd> <kbd>L</kbd> for the bottom knob.',
))


def knob_event(*, knob: str, action: str) -> KnobInput | None:
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
    KnobInput | None
        The event, or None if either value is not recognised.
    """
    if knob == BOTH:
        return KnobsHeld() if action == 'hold' else None
    if knob not in {member.value for member in Knob}:
        return None
    return member_event(knob=Knob(knob), action=action)


def member_event(*, knob: Knob, action: str) -> KnobInput | None:
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
    KnobInput | None
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


def live_page(*, nav: Sequence[NavItem], hint: str) -> str:
    """
    Draw the live page with its nav and knob hint.

    Parameters
    ----------
    nav : Sequence[NavItem]
        The nav's links, in order.
    hint : str
        The knob hint's lines, indented; trusted markup, not escaped.

    Returns
    -------
    str
        The page. The hint fills the last hint mark, so a nav label
        that reads like the mark stays as text.
    """
    page = page_frame(name='live.html', nav=nav, current='/')
    return hint.join(page.rsplit(HINT_MARK, 1))


class LiveView:  # pylint: disable=too-many-instance-attributes
    """
    The latest frame, pushed to browsers, and knob buttons sent back.

    Browsers follow new frames over server-sent events and send knob
    presses back as POSTs.

    Parameters
    ----------
    knobs : Callable[[KnobInput], None]
        Called with each knob event, when the request comes in.
    status : Callable[[], Mapping[str, object]]
        Extra fields for the status JSON.
    nav : Sequence[NavItem]
        The page's nav links, in order.
    hint : str
        The knob hint's lines, indented; trusted markup.

    Attributes
    ----------
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
        knobs: Callable[[KnobInput], None],
        status: Callable[[], Mapping[str, object]] = dict,
        nav: Sequence[NavItem] = KIT_NAV,
        hint: str = KNOB_HINT,
    ) -> None:
        self._knobs = knobs
        self._status = status
        self._nav = tuple(nav)
        self._hint = hint
        self._png: bytes | None = None
        self._started = time.monotonic()
        self._closing = False
        self.boot = f'{time.time_ns():x}'
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

    def close(self) -> None:
        """Release the open event streams."""
        self._closing = True
        self.changed.set()

    def _page_route(self) -> web.RouteDef:
        """
        Give the page's route.

        Returns
        -------
        web.RouteDef
            `GET /`.
        """
        return web.get('/', self._page)

    def _routes(self) -> list[web.RouteDef]:
        """
        List the live view's other routes.

        Returns
        -------
        list[web.RouteDef]
            The script, the frame, the event stream, the knob post and
            the status JSON.
        """
        return [
            web.get('/live.js', self._js),
            web.get('/frame.png', self._frame),
            web.get('/events', self._events),
            web.post('/knob', self._knob),
            web.get('/status', self._status_json),
        ]

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
            text=live_page(nav=self._nav, hint=self._hint),
            content_type='text/html',
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
            text=shell_text(name='live.js'),
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
        self._knobs(event)
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
            The status fields, then the frame version, uptime and peak
            memory.
        """
        del request
        return web.json_response({
            **self._status(),
            'version': self.version,
            'uptime_s': time.monotonic() - self._started,
            'max_rss_mb': max_rss_mb(),
        })
