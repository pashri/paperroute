"""Draw the page that searches for a pin's or a connection's first stop."""

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
CONNECT_FIELD: Final[str] = """
      <input type="hidden" name="connect" value="1">"""


class StopJson(TypedDict):
    """One stop in the search JSON."""

    id: str
    name: str
    kinds: list[str]
    routes: list[str]


def stops_json(
    *, options: PinOptions | None, text: str,
) -> list[StopJson]:
    """
    Search for stops and list them for the page script.

    Parameters
    ----------
    options : PinOptions | None
        The timetable's choices, or None while it is not ready.
    text : str
        The text searched for.

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
        for match in options.search(text=text)[:STOPS_SHOWN]
    ]


def search_page(
    *, options: PinOptions | None, query: Mapping[str, str],
) -> str:
    """
    Search for a stop or station and draw what was found.

    Parameters
    ----------
    options : PinOptions | None
        The timetable's choices, or None while it is not ready.
    query : Mapping[str, str]
        The query: `q`, and `connect=1` for a connection's first stop.

    Returns
    -------
    str
        The page; each result opens the pin editor, or the connection
        editor for a connection search.
    """
    text = query.get('q', '')
    connect = query.get('connect') == '1'
    title = 'Add a connection' if connect else 'Add a pin'
    if options is None:
        return edit_page(title=title, notice='', body=STARTING)
    return edit_page(
        title=title,
        notice='',
        body=search_results(
            text=text,
            found=options.search(text=text),
            target='/pins/connect' if connect else '/pins/edit',
            extra=CONNECT_FIELD if connect else '',
        ),
    )
