"""Cambridge's board pages: nav, hint, credits and status line."""

from collections.abc import Mapping
from typing import Final

from paperroute_cambridge.cambridge_feeds import POSITIONS_FEED
from paperroute_device.adapters.web_frame import NavItem
from paperroute_device.adapters.web_live import status_number
from paperroute_transit.adapters.board_head import BOARD_NAV, knob_hint

CAMBRIDGE_NAV: Final[tuple[NavItem, ...]] = BOARD_NAV
CAMBRIDGE_HINT: Final[str] = knob_hint(vehicle='bus')
CAMBRIDGE_CREDITS: Final[tuple[str, ...]] = (
    'Bus and coach data: contains public sector information licensed '
    'under the <a href="https://www.nationalarchives.gov.uk/doc/'
    'open-government-licence/version/3/">Open Government Licence '
    'v3.0</a>.',
    'Train timetables: Rail Delivery Group and National Rail '
    'Enquiries, through <a href="https://github.com/planarnetwork/'
    'gb-transit">gb-transit</a>. Station data: Department for '
    'Transport.',
    'Map data © <a href="https://www.openstreetmap.org/copyright">'
    'OpenStreetMap</a> contributors.',
)


def cambridge_status_line(*, status: Mapping[str, object]) -> str:
    """
    Write the live page's status line: frame age, render and parse.

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
    positions = (
        feeds.get(POSITIONS_FEED) if isinstance(feeds, Mapping) else None
    )
    parse = (
        positions.get('parse_ms')
        if isinstance(positions, Mapping)
        else None
    )
    return (
        f'Frame age {status_number(status.get("frame_age_s"))} s, '
        f'render {status_number(status.get("last_render_ms"))} ms, '
        f'positions parse {status_number(parse)} ms'
    )


def cambridge_suggest(*, live: bool) -> None:
    """
    Give the ways page no suggestions.

    Parameters
    ----------
    live : bool
        Whether the feeds are live; unused.
    """
    del live
