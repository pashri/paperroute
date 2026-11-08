"""Serve the frame and knob buttons to a browser on the local network."""

from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import TYPE_CHECKING, Final, Protocol

import numpy as np
from aiohttp import web
from numpy.typing import NDArray

from paperroute.adapters.board_head import BoardHead
from paperroute.adapters.mode_css import board_css
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
from paperroute.core.saved import Settings, default_settings
from paperroute_device.adapters.web_common import (
    asset,
    asset_bytes,
    form_fields,
)
from paperroute_device.adapters.web_frame import KIT_NAV, NavItem
from paperroute_device.adapters.web_guard import RemoteOf, peer_address
from paperroute_device.adapters.web_live import KNOB_HINT, LiveView
from paperroute_device.adapters.web_shell import web_app
from paperroute_hardware.ports import CLEAN_AFTER
from paperroute_transit.core.city import City

if TYPE_CHECKING:
    from paperroute_device.adapters.web_device import DevicePages

VENDORED: Final[dict[str, str]] = {
    'tom-select.complete.min.js': 'text/javascript',
    'tom-select.css': 'text/css',
}
RESET_HINT: Final[str] = (
    'Reset settings clears pins, settings, Wi-Fi and the key. Erase '
    'everything also deletes the timetable. The map is kept. Both are '
    'confirmed on the device.'
)


class StatusLine(Protocol):
    """Writes the live page's status line from the status fields."""

    def __call__(self, *, status: Mapping[str, object]) -> str:
        """
        Write the line.

        Parameters
        ----------
        status : Mapping[str, object]
            The status fields the app reported.

        Returns
        -------
        str
            The line, as plain text.
        """


def status_settings(
    *, status: Mapping[str, object], defaults: Settings,
) -> dict[str, str]:
    """
    Read the settings fields the app reported, over the defaults.

    Parameters
    ----------
    status : Mapping[str, object]
        The status page's extra fields.
    defaults : Settings
        The settings shown until the app reports.

    Returns
    -------
    dict[str, str]
        The form values; the defaults until the app reports.
    """
    values = form_values(settings=defaults)
    reported = status.get('settings')
    if isinstance(reported, Mapping):
        values.update({
            key: value
            for key, value in reported.items()
            if isinstance(value, str)
        })
    return values


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


class BoardRoutes(Protocol):
    """The pages a board adds to the web display."""

    def routes(
        self,
        *,
        send: Callable[[Event], None],
        reported: Callable[[], Mapping[str, str]],
    ) -> list[web.RouteDef]:
        """
        List the routes.

        Parameters
        ----------
        send : Callable[[Event], None]
            Sends an event to the app.
        reported : Callable[[], Mapping[str, str]]
            Gives the settings form values the app last reported.

        Returns
        -------
        list[web.RouteDef]
            The routes, in registration order.
        """

    async def close(self) -> None:
        """Release anything the pages hold open."""


class WebDisplay:
    """
    A display that serves the latest frame over HTTP.

    Only private and loopback peers are served. The live view shows
    the frames and sends knob presses back to the app.

    Parameters
    ----------
    city : City
        The city, whose default settings show until the app reports.
    status : Callable[[], dict[str, object]] | None
        Extra fields for the status page.
    remote_of : RemoteOf
        Finds the peer address of a request.
    device : DevicePages | None
        The device's own pages, such as Wi-Fi setup, or None.
    board : BoardRoutes | None
        The board's own pages, such as pins, or None.
    nav : Sequence[NavItem]
        The live page's nav links, in order.
    hint : str
        The live page's knob hint lines, indented; trusted markup.
    status_line : StatusLine | None
        Writes the live page's status line into the status as `line`,
        or None for no line.
    sheets : Sequence[str]
        The city's stylesheets in its web directory, each served at
        `/<name>` and linked by every page straight after `/live.css`.

    Attributes
    ----------
    device : DevicePages | None
        The device's own pages; set before `app` is called.
    live : LiveView
        The live view, which serves the frames and the knob buttons.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        city: City,
        status: Callable[[], dict[str, object]] | None = None,
        remote_of: RemoteOf = peer_address,
        device: 'DevicePages | None' = None,
        board: BoardRoutes | None = None,
        nav: Sequence[NavItem] = KIT_NAV,
        hint: str = KNOB_HINT,
        status_line: StatusLine | None = None,
        sheets: Sequence[str] = (),
    ) -> None:
        self.defaults = default_settings(city=city)
        self._modes = city.modes
        self._head = BoardHead(sheets=tuple(sheets))
        self._status = status
        self._status_line = status_line
        self.device = device
        self._board = board
        self._remote_of = remote_of
        self._post: Callable[[Event], None] = lambda event: None
        self._posted: dict[str, str] = {}
        self._before: dict[str, str] = {}
        self.live = LiveView(
            knobs=self.send,
            status=self._live_status,
            nav=nav,
            hint=hint,
            sheets=self._head.sheets,
        )

    def _live_status(self) -> dict[str, object]:
        """
        Give the live view's status fields, with the status line.

        Returns
        -------
        dict[str, object]
            The status fields, plus `line` when there is a status line.
        """
        snapshot = self._status() if self._status else {}
        if self._status_line is None:
            return snapshot
        return {**snapshot, 'line': self._status_line(status=snapshot)}

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
        Pass a frame to the live view.

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
        self.live.show(frame=frame)

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
        board = (
            self._board.routes(send=self.send, reported=self._reported)
            if self._board is not None
            else []
        )
        app = web_app(
            live=self.live,
            routes=[
                web.get('/board.css', self._board_css),
                *(
                    web.get(f'/{name}', self._sheet)
                    for name in self._head.sheets
                ),
                web.get('/vendor/{name}', self._vendor),
                web.get('/settings', self._settings_page),
                web.get('/settings.js', self._settings_js),
                web.post('/settings', self._settings),
                *board,
            ],
            device=self.device,
            remote_of=self._remote_of,
        )
        app.on_shutdown.append(self._close)
        return app

    async def _close(self, app: web.Application) -> None:
        """
        Close the board's pages.

        Parameters
        ----------
        app : web.Application
            The application being shut down.
        """
        del app
        if self._board is not None:
            await self._board.close()

    async def _board_css(self, request: web.Request) -> web.Response:
        """
        Serve the board pages' stylesheet.

        Parameters
        ----------
        request : web.Request
            The incoming request.

        Returns
        -------
        web.Response
            The stylesheet, its modes mark filled with the city's chip
            rules.
        """
        del request
        return web.Response(
            text=board_css(text=asset(name='board.css'), modes=self._modes),
            content_type='text/css',
        )

    async def _sheet(self, request: web.Request) -> web.Response:
        """
        Serve one of the city's stylesheets.

        Parameters
        ----------
        request : web.Request
            The incoming request, for a sheet's path.

        Returns
        -------
        web.Response
            The stylesheet from the app's web directory.
        """
        return web.Response(
            text=asset(name=request.path.removeprefix('/')),
            content_type='text/css',
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

    def _reported(self) -> dict[str, str]:
        """
        Read the settings the app last reported, over the defaults.

        Returns
        -------
        dict[str, str]
            The form values.
        """
        return status_settings(
            status=self._status() if self._status else {},
            defaults=self.defaults,
        )

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
            template=self._head.fill(page=asset(name='settings.html')),
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
        self._posted = posted_values(
            fields=fields, event=event, defaults=self.defaults,
        )
        self._post(event)
        if as_json:
            return web.Response(status=HTTPStatus.NO_CONTENT)
        raise web.HTTPSeeOther(location='/settings')
