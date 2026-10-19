"""How much of the strip a vehicle badge covers where it is drawn."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from paperroute.core.map_view import Marker

GLYPH_WIDTH: Final[Mapping[str, float]] = {  # badge text, rounded up
    '+': 9.0, '-': 6.0, '/': 7.5, '0': 9.5, '1': 5.75, '2': 8.75, '3': 8.5,
    '4': 9.25, '5': 8.75, '6': 8.75, '7': 7.75, '8': 9.0, '9': 8.75,
    'A': 10.25, 'B': 10.0, 'C': 9.0, 'D': 10.25, 'E': 9.0, 'F': 8.25,
    'G': 9.75, 'H': 10.25, 'I': 4.25, 'J': 8.0, 'K': 10.25, 'L': 8.0,
    'M': 11.5, 'N': 10.25, 'O': 10.25, 'P': 9.0, 'Q': 10.25, 'R': 10.0,
    'S': 8.75, 'T': 8.75, 'U': 10.25, 'V': 9.75, 'W': 12.0, 'X': 10.0,
    'Y': 10.0, 'Z': 9.0, 'a': 8.0, 'b': 8.25, 'c': 8.0, 'd': 8.25, 'e': 8.0,
    'f': 5.25, 'g': 8.25, 'h': 8.5, 'i': 4.25, 'j': 4.25, 'k': 8.0, 'l': 4.25,
    'm': 12.5, 'n': 8.5, 'o': 8.0, 'p': 8.25, 'q': 8.25, 'r': 6.0, 's': 7.0,
    't': 5.75, 'u': 8.5, 'v': 8.25, 'w': 10.75, 'x': 8.5, 'y': 8.25, 'z': 8.0,
}
WIDEST_GLYPH: Final[float] = 12.5  # any glyph not in the table
BADGE_PAD: Final[float] = 5.0  # a badge's box past its text, each side
BADGE_HALF: Final[float] = 10.0  # half a badge's height
WEDGE_REACH: Final[float] = 10.0  # a live badge's wedge past its box
TAB_REACH: Final[float] = 12.0  # a stale badge's age tab below its box
TAB_HALF: Final[float] = 15.0  # half the widest age tab, outline included
OUTLINE: Final[float] = 1.0  # the paper edge drawn round a badge


@dataclass(frozen=True)
class Extent:
    """
    The box a badge's drawing covers, wedge and age tab included.

    Attributes
    ----------
    left : float
        Left edge.
    right : float
        Right edge.
    top : float
        Top edge.
    bottom : float
        Bottom edge.
    """

    left: float
    right: float
    top: float
    bottom: float


def half_width(*, route: str) -> float:
    """
    Give a badge's half width, never less than drawn.

    Parameters
    ----------
    route : str
        Route label.

    Returns
    -------
    float
        Half the label's width from `GLYPH_WIDTH`, plus `BADGE_PAD` and
        the outline.
    """
    text = sum(GLYPH_WIDTH.get(glyph, WIDEST_GLYPH) for glyph in route)
    return text / 2 + BADGE_PAD + OUTLINE


def reaches(*, marker: Marker) -> tuple[float, float]:
    """
    Measure how far a badge's drawing reaches above and below its centre.

    Parameters
    ----------
    marker : Marker
        Badge.

    Returns
    -------
    tuple[float, float]
        Pixels above and below: half the badge and its outline, or its
        wedge on the side a live badge points to, or its age tab below.
    """
    heading = (
        0.0
        if marker.bearing is None or not marker.live
        else round(math.cos(math.radians(marker.bearing)), 6)
    )
    up = BADGE_HALF + max(OUTLINE, WEDGE_REACH if heading > 0 else 0.0)
    down = BADGE_HALF + max(
        OUTLINE,
        WEDGE_REACH if heading < 0 else 0.0,
        0.0 if marker.age is None else TAB_REACH,
    )
    return up, down


def extent_of(*, marker: Marker) -> Extent:
    """
    Give the box a badge's drawing covers.

    Parameters
    ----------
    marker : Marker
        Badge.

    Returns
    -------
    Extent
        Its badge, at least as wide as an age tab when it has one, and
        its wedge and tab above and below.
    """
    half = max(
        half_width(route=marker.route),
        0.0 if marker.age is None else TAB_HALF,
    )
    up, down = reaches(marker=marker)
    return Extent(
        left=marker.x - half,
        right=marker.x + half,
        top=marker.y - up,
        bottom=marker.y + down,
    )
