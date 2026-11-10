"""The board's routes on the device kit's web display."""

from collections.abc import Callable, Mapping, Sequence
from typing import Final

from aiohttp import web

from paperroute_device.adapters.web_display import KitRoutes
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.adapters.board_web import board_bytes, board_text
from paperroute_transit.adapters.mode_css import board_css
from paperroute_transit.core.modes import ModeSpec

VENDORED: Final[dict[str, str]] = {
    'tom-select.complete.min.js': 'text/javascript',
    'tom-select.css': 'text/css',
}
RESET_HINT: Final[str] = (
    'Reset settings clears pins, settings, Wi-Fi and the key. Erase '
    'everything also deletes the timetable. The map is kept. Both are '
    'confirmed on the device.'
)


class BoardRoutes:
    """
    The board's stylesheet, vendored files and pages, with the kit's.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        The city's modes, whose chip rules the stylesheet holds.
    pins : PinPages
        The pin, connection, ways and plug pages.
    reported : Callable[[], Mapping[str, str]]
        Gives the settings form values the board last reported.
    """

    def __init__(
        self,
        *,
        modes: Sequence[ModeSpec],
        pins: PinPages,
        reported: Callable[[], Mapping[str, str]],
    ) -> None:
        self._modes = tuple(modes)
        self._pins = pins
        self._reported = reported

    def routes(self, *, kit: KitRoutes) -> list[web.RouteDef]:
        """
        List the routes in the order the board has always served them.

        Parameters
        ----------
        kit : KitRoutes
            The kit's sheet and Settings routes, and its post.

        Returns
        -------
        list[web.RouteDef]
            `/board.css`, the kit's sheets, `/vendor/{name}`, the
            Settings page, then the pin pages.
        """
        return [
            web.get('/board.css', self._board_css),
            *kit.sheets,
            web.get('/vendor/{name}', vendored),
            *kit.settings,
            *self._pins.routes(send=kit.post, reported=self._reported),
        ]

    async def close(self) -> None:
        """Close the pin pages' plug."""
        await self._pins.close()

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
            text=board_css(
                text=board_text(name='board.css'),
                modes=self._modes,
            ),
            content_type='text/css',
        )


async def vendored(request: web.Request) -> web.Response:
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
        body=board_bytes(name=f'vendor/{name}'),
        content_type=VENDORED[name],
        headers={'Cache-Control': 'max-age=86400'},
    )
