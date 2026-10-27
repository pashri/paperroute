"""Draw the page that searches for a pin's first stop."""

from collections.abc import Mapping
from typing import Final, TypedDict

from paperroute.adapters.web_pin_edit import (
    MIN_QUERY,
    edit_page,
    search_results,
)
from paperroute.ports import PinOptions

STOPS_SHOWN: Final[int] = 20
STARTING: Final[str] = '<p>Starting up. Reload in a moment.</p>'
TITLE: Final[str] = 'Add a pin'


class StopJson(TypedDict):
    """One stop in the search JSON."""

    id: str
    name: str
    kinds: list[str]
    routes: list[str]


def stops_json(
    *, options: PinOptions | None, text: str, skip_school: bool = True,
) -> list[StopJson]:
    """
    Search for stops and list them for the page script.

    Parameters
    ----------
    options : PinOptions | None
        The timetable's choices, or None while it is not ready.
    text : str
        The text searched for.
    skip_school : bool
        Whether school routes are left out of each stop's routes.

    Returns
    -------
    list[StopJson]
        At most `STOPS_SHOWN` stops; empty for text under two characters
        once trimmed, or without a timetable.
    """
    if options is None or len(text.strip()) < MIN_QUERY:
        return []
    return [
        StopJson(
            id=match.place.stop_id,
            name=match.place.name,
            kinds=[str(kind) for kind in match.kinds],
            routes=list(match.routes),
        )
        for match in options.search(
            text=text, skip_school=skip_school,
        )[:STOPS_SHOWN]
    ]


def search_page(
    *,
    options: PinOptions | None,
    query: Mapping[str, str],
    skip_school: bool = True,
) -> str:
    """
    Search for a stop or station and draw what was found.

    Parameters
    ----------
    options : PinOptions | None
        The timetable's choices, or None while it is not ready.
    query : Mapping[str, str]
        The query: `q`.
    skip_school : bool
        Whether school routes are left out of each stop's routes.

    Returns
    -------
    str
        The page; each result opens the pin editor.
    """
    text = query.get('q', '')
    if options is None:
        return edit_page(title=TITLE, notice='', body=STARTING)
    return edit_page(
        title=TITLE,
        notice='',
        body=search_results(
            text=text,
            found=options.search(text=text, skip_school=skip_school),
        ),
    )
