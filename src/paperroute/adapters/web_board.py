"""Sydney's board pages: its nav, hint and Trip Planner suggestions."""

from collections.abc import Callable
from datetime import datetime
from typing import Final

from paperroute.adapters.trip_planner import trip_planner_for
from paperroute.adapters.web_suggest import SuggestPages, sydney_now
from paperroute.adapters.web_ways import WaysPages
from paperroute.planner_port import TripPlanner
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


class SuggestPlug:
    """
    Sydney's Trip Planner suggestions on the ways page.

    Parameters
    ----------
    planner : TripPlanner | None
        The Trip Planner for suggested ways, or None.
    now : Callable[[], datetime]
        Gives the current time, for the default trip search.
    """

    def __init__(
        self,
        *,
        planner: TripPlanner | None,
        now: Callable[[], datetime] = sydney_now,
    ) -> None:
        self._planner = planner
        self._now = now

    def pages(
        self, *, ways: WaysPages, skip_school: Callable[[], bool],
    ) -> SuggestPages:
        """
        Build the suggestion list around the ways page.

        Parameters
        ----------
        ways : WaysPages
            The ways page.
        skip_school : Callable[[], bool]
            Says whether school buses are left out, as last reported.

        Returns
        -------
        SuggestPages
            The suggestion pages.
        """
        return SuggestPages(
            ways=ways,
            planner=self._planner,
            now=self._now,
            skip_school=skip_school,
        )

    async def close(self) -> None:
        """Close the Trip Planner."""
        if self._planner is not None:
            await self._planner.close()


def sydney_suggest(*, live: bool) -> SuggestPlug:
    """
    Make Sydney's suggestion plug.

    Parameters
    ----------
    live : bool
        Whether the feeds are live, which the Trip Planner needs.

    Returns
    -------
    SuggestPlug
        The plug; with replayed feeds it has no Trip Planner but keeps
        its pages.
    """
    return SuggestPlug(planner=trip_planner_for(live=live))
