"""Draw the page that searches for a pin's or a connection's first stop."""

from collections.abc import Mapping
from typing import Final

from paperroute.adapters.web_pin_edit import edit_page, search_results
from paperroute.ports import PinOptions

STARTING: Final[str] = '<p>Starting up. Reload in a moment.</p>'
CONNECT_FIELD: Final[str] = """
      <input type="hidden" name="connect" value="1">"""


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
