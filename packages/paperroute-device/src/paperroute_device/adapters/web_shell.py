"""Build the device's guarded web app around the live view."""

from collections.abc import Sequence

from aiohttp import web

from paperroute_device.adapters.web_device import DevicePages
from paperroute_device.adapters.web_frame import shell_bytes, shell_text
from paperroute_device.adapters.web_guard import (
    RemoteOf,
    local_only,
    peer_address,
)
from paperroute_device.adapters.web_live import LiveView


async def css(request: web.Request) -> web.Response:
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
        text=shell_text(name='live.css'),
        content_type='text/css',
    )


async def font(request: web.Request) -> web.Response:
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
        body=shell_bytes(name='fonts/public-sans.woff2'),
        content_type='font/woff2',
        headers={'Cache-Control': 'max-age=86400'},
    )


def shell_routes() -> list[web.RouteDef]:
    """
    List the shell's static routes.

    Returns
    -------
    list[web.RouteDef]
        The stylesheet and the page font.
    """
    return [
        web.get('/live.css', css),
        web.get('/fonts/public-sans.woff2', font),
    ]


def web_app(
    *,
    live: LiveView,
    routes: Sequence[web.RouteDef] = (),
    device: DevicePages | None,
    remote_of: RemoteOf = peer_address,
) -> web.Application:
    """
    Build the web app, every route behind the local-network check.

    Parameters
    ----------
    live : LiveView
        The live view, served at `/`; closed when the app shuts down.
    routes : Sequence[web.RouteDef]
        The app's own routes, registered after the live view's.
    device : DevicePages | None
        The device's own pages, registered last, or None.
    remote_of : RemoteOf
        Finds the peer address of a request.

    Returns
    -------
    web.Application
        The app, with the live view's close first in `on_shutdown`.
    """

    async def _close_live(app: web.Application) -> None:
        """
        Close the live view.

        Parameters
        ----------
        app : web.Application
            The application being shut down.
        """
        del app
        live.close()

    app = web.Application(middlewares=[local_only(remote_of=remote_of)])
    app.on_shutdown.append(_close_live)
    app.add_routes([
        live._page_route(),  # pylint: disable=protected-access  # the guarded builder is the one caller
        *shell_routes(),
        *live._routes(),  # pylint: disable=protected-access  # the guarded builder is the one caller
        *routes,
    ])
    if device is not None:
        app.add_routes(
            device._routes(),  # pylint: disable=protected-access  # the guarded builder is the one caller
        )
    return app
