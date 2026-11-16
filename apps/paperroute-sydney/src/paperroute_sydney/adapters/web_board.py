"""Sydney's board pages: nav, hint, status line, Trip Planner suggestions."""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final

from paperroute_device.adapters.web_frame import NavItem
from paperroute_device.adapters.web_live import status_number
from paperroute_sydney.adapters.trip_planner import trip_planner_for
from paperroute_sydney.adapters.web_suggest import SuggestPages, sydney_now
from paperroute_sydney.city import SydneyFeed
from paperroute_sydney.planner_port import TripPlanner
from paperroute_sydney.sydney_feeds import FEEDS
from paperroute_transit.adapters.board_head import BOARD_NAV, knob_hint
from paperroute_transit.adapters.web_ways import WaysPages

SYDNEY_NAV: Final[tuple[NavItem, ...]] = BOARD_NAV
SYDNEY_HINT: Final[str] = knob_hint(vehicle='train')


def sydney_status_line(*, status: Mapping[str, object]) -> str:
    """
    Write the live page's status line: frame age, render and bus parse.

    Parameters
    ----------
    status : Mapping[str, object]
        The status fields; a missing or malformed part shows as a dash.

    Returns
    -------
    str
        The line, each number rounded half up to a whole one.
    """
    feeds = status.get('feeds')
    buses = (
        feeds.get(FEEDS[SydneyFeed.BUS])
        if isinstance(feeds, Mapping)
        else None
    )
    parse = buses.get('parse_ms') if isinstance(buses, Mapping) else None
    return (
        f'Frame age {status_number(status.get("frame_age_s"))} s, '
        f'render {status_number(status.get("last_render_ms"))} ms, '
        f'bus parse {status_number(parse)} ms'
    )


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
