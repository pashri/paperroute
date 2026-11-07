"""A route's fill and text colours, as the timetable gives them."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

HEX: Final[re.Pattern[str]] = re.compile(r'[0-9a-f]{6}')
DARK_TEXT: Final[str] = '000000'
LIGHT_TEXT: Final[str] = 'ffffff'
BLACK_ABOVE: Final[float] = 0.179  # relative luminance


@dataclass(frozen=True, order=True)
class RouteColour:
    """
    A route's colours.

    Attributes
    ----------
    fill : str
        Fill, six lower-case hex digits without a ``#``.
    text : str
        Text colour, in the same form.
    """

    fill: str
    text: str


def hex_of(*, value: str | None) -> str | None:
    """
    Read a colour of six hex digits.

    Parameters
    ----------
    value : str | None
        The timetable's value.

    Returns
    -------
    str | None
        The digits in lower case; None for anything else, such as a
        leading ``#`` or three digits.
    """
    folded = (value or '').lower()
    return folded if HEX.fullmatch(folded) else None


def luminance(*, colour: str) -> float:
    """
    Work out a colour's WCAG relative luminance.

    Parameters
    ----------
    colour : str
        Six lower-case hex digits.

    Returns
    -------
    float
        Luminance from 0 (black) to 1 (white).
    """
    channels = [
        int(colour[start:start + 2], 16) / 255 for start in (0, 2, 4)
    ]
    linear = [
        (
            each / 12.92
            if each <= 0.04045
            else ((each + 0.055) / 1.055) ** 2.4
        )
        for each in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def colour_of(*, fill: str | None, text: str | None) -> RouteColour | None:
    """
    Make a route's colours from the timetable's values.

    Parameters
    ----------
    fill : str | None
        The route's ``color``.
    text : str | None
        The route's ``text_color``.

    Returns
    -------
    RouteColour | None
        None for a missing or invalid fill. A missing or invalid text
        colour becomes black or white, whichever contrasts more.
    """
    fill_hex = hex_of(value=fill)
    if fill_hex is None:
        return None
    text_hex = hex_of(value=text)
    if text_hex is None:
        dark = luminance(colour=fill_hex) > BLACK_ABOVE
        text_hex = DARK_TEXT if dark else LIGHT_TEXT
    return RouteColour(fill=fill_hex, text=text_hex)


def common_colours(
    *, rows: Iterable[tuple[str, str | None, str | None, int]],
) -> dict[str, RouteColour]:
    """
    Pick each route name's most common valid colours.

    Parameters
    ----------
    rows : Iterable[tuple[str, str | None, str | None, int]]
        Short name, ``color``, ``text_color`` and how many routes have
        them.

    Returns
    -------
    dict[str, RouteColour]
        For each name with a valid colour, its most common pair; a tie
        goes to the lower fill.
    """
    counts: Counter[tuple[str, RouteColour]] = Counter()
    for name, fill, text, count in rows:
        colour = colour_of(fill=fill, text=text)
        if colour is not None:
            counts[(name, colour)] += count
    ranked = sorted(
        counts.items(), key=lambda item: (-item[1], item[0][1]),
    )
    picked: dict[str, RouteColour] = {}
    for (name, colour), _ in ranked:
        picked.setdefault(name, colour)
    return picked
