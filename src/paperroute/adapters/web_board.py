"""Serve the board's own pages: pins, ways, connections and suggestions."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Final, Protocol

from aiohttp import web

from paperroute.adapters.web_connection import ConnectionPages
from paperroute.adapters.web_pins import PinsPages
from paperroute.adapters.web_suggest import SuggestPages
from paperroute.adapters.web_ways import WaysPages
from paperroute.city import SYDNEY_ZONE
from paperroute.core.messages import Event
from paperroute.observers import PinsSeen
from paperroute.planner_port import TripPlanner
from paperroute.ports import PinOptions
from paperroute_device.adapters.web_frame import DEVICE_NAV, LIVE_NAV, NavItem

PINS_NAV: Final[NavItem] = NavItem(
    href='/pins',
    label='Pins',
    icon='<path d="M12 22s7-7 7-12a7 7 0 0 0-14 0c0 5 7 12 7 12z"/>'
    '<circle cx="12" cy="10" r="2.5"/>',
)
SETTINGS_NAV: Final[NavItem] = NavItem(
    href='/settings',
    label='Settings',
    icon='<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/>'
    '<circle cx="15" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
)
SYDNEY_NAV: Final[tuple[NavItem, ...]] = (
    LIVE_NAV,
    PINS_NAV,
    SETTINGS_NAV,
    DEVICE_NAV,
)
SYDNEY_HINT: Final[str] = '\n'.join((
    '        Keys: <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd> for the top knob,',
    '        <kbd>J</kbd> <kbd>K</kbd> <kbd>L</kbd> for the bottom knob;',
    '        hold <kbd>K</kbd>, or double-click or hold Press, to pin.',
    '        Hold <kbd>S</kbd> and <kbd>K</kbd> together, or both Press',
    '        buttons, for Settings.',
    '        On the Strip, turn while the bottom knob is held to pick a'
    ' train:',
    '        hold <kbd>K</kbd> or Press, then press <kbd>J</kbd> or',
    '        <kbd>L</kbd>, or tap Left or Right with another finger.',
))


class BoardPages(Protocol):
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


class SydneyPages:
    """
    Sydney's pin, connection, ways and suggestion pages.

    Parameters
    ----------
    pins : Callable[[], PinsSeen | None]
        Gives the pins the app last reported; None until it does.
    options : PinOptions | None
        The timetable's choices for the pin editor, or None.
    planner : TripPlanner | None
        The Trip Planner for suggested ways, or None.
    now : Callable[[], datetime]
        Gives the current time, for the default trip search.

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
        planner: TripPlanner | None,
        now: Callable[[], datetime] = lambda: datetime.now(SYDNEY_ZONE),
    ) -> None:
        self._pins = pins
        self._options = options
        self._planner = planner
        self._now = now
        self.pins_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def routes(
        self,
        *,
        send: Callable[[Event], None],
        reported: Callable[[], Mapping[str, str]],
    ) -> list[web.RouteDef]:
        """
        List the routes of the pin, connection, ways and suggestion pages.

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
            sleep=self.pins_sleep,
            skip_school=skip_school,
        )
        ways = WaysPages(pages=pins, pins=self._pins, options=self._options)
        suggest = SuggestPages(
            ways=ways,
            planner=self._planner,
            now=self._now,
            skip_school=skip_school,
        )
        ways.card = suggest.card
        return [
            *pins.routes(),
            *ConnectionPages(pins=self._pins, options=self._options).routes(),
            *ways.routes(),
            *suggest.routes(),
        ]

    async def close(self) -> None:
        """Close the Trip Planner."""
        if self._planner is not None:
            await self._planner.close()
