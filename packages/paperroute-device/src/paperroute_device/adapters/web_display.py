"""Serve the frame, the knob buttons and the device's pages on the LAN."""

# A copy of the board app's display, until it uses this one.
# pylint: disable=duplicate-code

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

import numpy as np
from aiohttp import web
from numpy.typing import NDArray

from paperroute_device.adapters.web_common import asset
from paperroute_device.adapters.web_frame import KIT_NAV, NavItem
from paperroute_device.adapters.web_guard import RemoteOf, peer_address
from paperroute_device.adapters.web_live import KNOB_HINT, LiveView
from paperroute_device.adapters.web_shell import web_app
from paperroute_hardware.ports import CLEAN_AFTER

if TYPE_CHECKING:
    from paperroute_device.adapters.settings_page import SettingsPage
    from paperroute_device.adapters.web_device import DevicePages

KIT_PATHS: Final[frozenset[str]] = frozenset({
    '/',
    '/live.css',
    '/fonts/{name}',
    '/live.js',
    '/frame.png',
    '/events',
    '/knob',
    '/status',
    '/settings',
    '/settings.js',
    '/device',
    '/setup',
    '/setup.js',
    '/power',
})
KIT_PREFIXES: Final[tuple[str, ...]] = ('/device/', '/fonts/')


def kit_shadowed(path: str) -> bool:
    """
    Say whether an app's route path would shadow one of the kit's.

    Parameters
    ----------
    path : str
        The route's path, as registered.

    Returns
    -------
    bool
        True for a kit path, any path under `/device/` or `/fonts/`, or
        a path whose first segment is dynamic, which would match every
        page at the root.
    """
    first = path.removeprefix('/').split('/', 1)[0]
    return (
        path in KIT_PATHS
        or path.startswith(KIT_PREFIXES)
        or first.startswith('{')
    )


@dataclass(frozen=True)
class KitRoutes:
    """
    The kit's routes an app places among its own.

    Attributes
    ----------
    sheets : list[web.RouteDef]
        One route per extra stylesheet, in link order.
    settings : list[web.RouteDef]
        The Settings page, its script and its post.
    """

    sheets: list[web.RouteDef]
    settings: list[web.RouteDef]


class AppRoutes(Protocol):
    """The pages an app adds to the web display."""

    def routes(self, *, kit: KitRoutes) -> list[web.RouteDef]:
        """
        List the app's routes with the kit's placed among them.

        Parameters
        ----------
        kit : KitRoutes
            The kit's routes; each must appear exactly once.

        Returns
        -------
        list[web.RouteDef]
            Every route, in registration order.
        """

    async def close(self) -> None:
        """Release anything the pages hold open."""


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


def placement_problems(
    *, placed: Sequence[web.RouteDef], kit: KitRoutes,
) -> list[str]:
    """
    Check an app's route list against the kit's routes.

    Parameters
    ----------
    placed : Sequence[web.RouteDef]
        The list the app gave.
    kit : KitRoutes
        The kit's routes it was handed.

    Returns
    -------
    list[str]
        One line per kit route dropped or repeated, and per app route
        that shadows a kit path; empty when the list is fine.
    """
    owned = [*kit.sheets, *kit.settings]
    counts = Counter(id(route) for route in placed)
    problems = [
        f'kit route {route.method} {route.path} placed '
        f'{counts[id(route)]} times'
        for route in owned
        if counts[id(route)] != 1
    ]
    kit_ids = {id(route) for route in owned}
    return problems + [
        f'app route {route.method} {route.path} shadows a kit path'
        for route in placed
        if id(route) not in kit_ids and kit_shadowed(route.path)
    ]


class WebDisplay:
    """
    A display that serves the latest frame over HTTP.

    Only private and loopback peers are served. The live view shows
    the frames and sends knob presses back to the kit.

    Parameters
    ----------
    status : Callable[[], dict[str, object]] | None
        Extra fields for the status page.
    remote_of : RemoteOf
        Finds the peer address of a request.
    device : DevicePages | None
        The device's own pages, such as Wi-Fi setup, or None.
    routes : AppRoutes | None
        The app's own pages, or None.
    nav : Sequence[NavItem]
        The live page's nav links, in order.
    hint : str
        The live page's knob hint lines, indented; trusted markup.
    status_line : StatusLine | None
        Writes the live page's status line into the status as `line`,
        or None for no line.
    sheets : Sequence[str]
        The app's stylesheets in its web directory, each served at
        `/<name>` and linked by every page straight after `/live.css`.
    settings : SettingsPage | None
        The Settings page, or None for none.

    Attributes
    ----------
    device : DevicePages | None
        The device's own pages; set before `app` is called.
    settings : SettingsPage | None
        The Settings page; set before `app` is called.
    live : LiveView
        The live view, which serves the frames and the knob buttons.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        status: Callable[[], dict[str, object]] | None = None,
        remote_of: RemoteOf = peer_address,
        device: 'DevicePages | None' = None,
        routes: AppRoutes | None = None,
        nav: Sequence[NavItem] = KIT_NAV,
        hint: str = KNOB_HINT,
        status_line: StatusLine | None = None,
        sheets: Sequence[str] = (),
        settings: 'SettingsPage | None' = None,
    ) -> None:
        self._status = status
        self._status_line = status_line
        self._sheets = tuple(sheets)
        self._routes = routes
        self._remote_of = remote_of
        self._post: Callable[[object], None] = lambda event: None
        self.device = device
        self.settings = settings
        self.live = LiveView(
            knobs=self.send,
            status=self._live_status,
            nav=nav,
            hint=hint,
            sheets=self._sheets,
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

    def bind(self, *, post: Callable[[object], None]) -> None:
        """
        Set where events from the browser go.

        Parameters
        ----------
        post : Callable[[object], None]
            Called with each event.
        """
        self._post = post

    def send(self, event: object) -> None:
        """
        Send an event to wherever `bind` last pointed.

        Parameters
        ----------
        event : object
            The event.
        """
        self._post(event)

    def routes(self) -> list[web.RouteDef]:
        """
        List the kit's sheet and Settings routes with the app's own.

        Returns
        -------
        list[web.RouteDef]
            The app's list, or the sheets then Settings with no app
            routes.

        Raises
        ------
        ValueError
            When the app's list drops or repeats a kit route, or holds a
            route that shadows a kit path.
        """
        kit = KitRoutes(
            sheets=[web.get(f'/{name}', self._sheet) for name in self._sheets],
            settings=self.settings.routes() if self.settings else [],
        )
        if self._routes is None:
            return [*kit.sheets, *kit.settings]
        placed = self._routes.routes(kit=kit)
        problems = placement_problems(placed=placed, kit=kit)
        if problems:
            raise ValueError('; '.join(problems))
        return placed

    def app(self) -> web.Application:
        """
        Build the web application.

        Returns
        -------
        web.Application
            The routes, behind the local-network check; power and the
            device's own pages only when there are device pages.

        Raises
        ------
        ValueError
            When the app's routes are misplaced, as `routes` says.
        """
        app = web_app(
            live=self.live,
            routes=self.routes(),
            device=self.device,
            remote_of=self._remote_of,
        )
        app.on_shutdown.append(self._close)
        return app

    async def _close(self, app: web.Application) -> None:
        """
        Close the app's pages.

        Parameters
        ----------
        app : web.Application
            The application being shut down.
        """
        del app
        if self._routes is not None:
            await self._routes.close()

    async def _sheet(self, request: web.Request) -> web.Response:
        """
        Serve one of the app's stylesheets.

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
