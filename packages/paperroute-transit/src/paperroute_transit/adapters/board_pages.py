"""Serve the pin, connection and ways pages, with an optional plug."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Final, Protocol

from aiohttp import web
from multidict import MultiMapping

from paperroute_device.adapters.web_common import asset
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.web_connection import ConnectionPages
from paperroute_transit.adapters.web_pins import PinsPages
from paperroute_transit.adapters.web_ways import WaysPages
from paperroute_transit.core.messages import Event
from paperroute_transit.core.modes import ModeSpec
from paperroute_transit.observers import PinsSeen
from paperroute_transit.ports import PinOptions

SUGGEST_SCRIPT: Final[str] = 'suggest.js'


class PagePlug(Protocol):
    """Pages a plug adds, and the card it draws on the ways page."""

    def card(self, query: MultiMapping[str]) -> str:
        """
        Draw the card shown on the ways page.

        Parameters
        ----------
        query : MultiMapping[str]
            The ways page's query.

        Returns
        -------
        str
            The card's HTML.
        """

    def routes(self) -> list[web.RouteDef]:
        """
        List the plug's own routes.

        Returns
        -------
        list[web.RouteDef]
            The routes, in registration order.
        """


class WaysPlug(Protocol):
    """Extra pages built on the ways page, such as trip suggestions."""

    def pages(
        self, *, ways: WaysPages, skip_school: Callable[[], bool],
    ) -> PagePlug:
        """
        Build the plug's pages around the ways page.

        Parameters
        ----------
        ways : WaysPages
            The ways page.
        skip_school : Callable[[], bool]
            Says whether school buses are left out, as last reported.

        Returns
        -------
        PagePlug
            The pages.
        """

    async def close(self) -> None:
        """Release anything the plug holds open."""


async def suggest_script(request: web.Request) -> web.Response:
    """
    Serve the suggestion script.

    Parameters
    ----------
    request : web.Request
        The incoming request.

    Returns
    -------
    web.Response
        The script.
    """
    del request
    return web.Response(
        text=asset(name=SUGGEST_SCRIPT), content_type='text/javascript',
    )


class PinPages:
    """
    The pin, connection and ways pages, and a plug's pages after them.

    With a plug, the pin editor also loads the suggestion script, served
    after the plug's own routes.

    Parameters
    ----------
    pins : Callable[[], PinsSeen | None]
        Gives the pins the app last reported; None until it does.
    options : PinOptions | None
        The timetable's choices for the pin editor, or None.
    modes : Sequence[ModeSpec]
        The city's modes, for the pins' route chips.
    plug : WaysPlug | None
        Adds pages and the ways page's card, or None for neither.
    head : BoardHead
        The extra stylesheets and scripts every page links, and its nav.

    Attributes
    ----------
    pins_sleep : Callable[[float], Awaitable[None]]
        How the Pins page waits for a new report; set before `routes`.
    """

    def __init__(
        self,
        *,
        pins: Callable[[], PinsSeen | None],
        options: PinOptions | None,
        modes: Sequence[ModeSpec],
        plug: WaysPlug | None,
        head: BoardHead,
    ) -> None:
        self._pins = pins
        self._options = options
        self._modes = modes
        self._plug = plug
        self._head = (
            head
            if plug is None
            else replace(head, scripts=(*head.scripts, SUGGEST_SCRIPT))
        )
        self.pins_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def routes(
        self,
        *,
        send: Callable[[Event], None],
        reported: Callable[[], Mapping[str, str]],
    ) -> list[web.RouteDef]:
        """
        List the routes of the pin, connection, ways and plug pages.

        Parameters
        ----------
        send : Callable[[Event], None]
            Sends an event to the app.
        reported : Callable[[], Mapping[str, str]]
            Gives the settings form values the app last reported.

        Returns
        -------
        list[web.RouteDef]
            The routes.
        """

        def skip_school() -> bool:
            """
            Say whether school buses are left out, as last reported.

            Returns
            -------
            bool
                The school switch.
            """
            return reported()['skip_school_buses'] == 'on'

        pins = PinsPages(
            post=send,
            pins=self._pins,
            options=self._options,
            modes=self._modes,
            sleep=self.pins_sleep,
            skip_school=skip_school,
            head=self._head,
        )
        ways = WaysPages(
            pages=pins,
            pins=self._pins,
            options=self._options,
            head=self._head,
        )
        plugged = (
            self._plug.pages(ways=ways, skip_school=skip_school)
            if self._plug is not None
            else None
        )
        if plugged is not None:
            ways.card = plugged.card
        return [
            *pins.routes(),
            *ConnectionPages(
                pins=self._pins, options=self._options, head=self._head,
            ).routes(),
            *ways.routes(),
            *(
                [*plugged.routes(), web.get('/suggest.js', suggest_script)]
                if plugged is not None
                else []
            ),
        ]

    async def close(self) -> None:
        """Close the plug."""
        if self._plug is not None:
            await self._plug.close()
