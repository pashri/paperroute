"""Choose the form of a name or headsign that fits a slot."""

from collections.abc import Sequence
from dataclasses import dataclass

from paperroute_device.render.canvas import Face, ellipsized, text_width
from paperroute_device.render.frame import wrap
from paperroute_transit.core.headsigns import headsign_forms
from paperroute_transit.core.names import NameRules, name_forms


@dataclass(frozen=True)
class Naming:
    """
    How names are shortened in one place.

    Attributes
    ----------
    rules : NameRules
        Word lists and options for shortening.
    siblings : tuple of str
        The other names on the same line or screen, as drawn before fitting.
    """

    rules: NameRules
    siblings: tuple[str, ...] = ()


def two_line_split(
    *,
    text: str,
    face: Face,
    width: float,
) -> tuple[str, str] | None:
    """
    Wrap text onto exactly two lines that each fit.

    Parameters
    ----------
    text : str
        Text.
    face : Face
        Font.
    width : float
        Available width in pixels.

    Returns
    -------
    tuple[str, str] or None
        The two lines, or None when wrapping gives another count or a
        line is too wide.
    """
    lines = wrap(text=text, face=face, width=width)
    if len(lines) != 2:
        return None
    if any(text_width(text=line, face=face) > width for line in lines):
        return None
    return (lines[0], lines[1])


def overflowed(
    *,
    text: str,
    face: Face,
    width: float,
    two_lines: bool,
) -> tuple[str, ...]:
    """
    Cut text that does not fit, with an ellipsis.

    Parameters
    ----------
    text : str
        Text.
    face : Face
        Font.
    width : float
        Available width in pixels.
    two_lines : bool
        Whether a second line is available.

    Returns
    -------
    tuple[str, ...]
        One line, or two when the first line fits and the rest is cut.
    """
    lines = wrap(text=text, face=face, width=width)
    if (
        two_lines
        and len(lines) >= 2
        and text_width(text=lines[0], face=face) <= width
    ):
        rest = ' '.join(lines[1:])
        return (lines[0], ellipsized(text=rest, face=face, width=width))
    return (ellipsized(text=text, face=face, width=width),)


def fit_forms(
    *,
    forms: Sequence[str],
    face: Face,
    width: float,
    two_lines: bool = False,
) -> tuple[str, ...]:
    """
    Pick the first form that fits, on one line or two.

    Parameters
    ----------
    forms : Sequence[str]
        Forms from the longest to the shortest.
    face : Face
        Font.
    width : float
        Available width in pixels.
    two_lines : bool
        Whether a form may wrap onto two lines.

    Returns
    -------
    tuple[str, ...]
        The lines shown; the last form is cut when none fits.
    """
    for form in forms:
        if text_width(text=form, face=face) <= width:
            return (form,)
        split = (
            two_line_split(text=form, face=face, width=width)
            if two_lines
            else None
        )
        if split is not None:
            return split
    return overflowed(
        text=forms[-1], face=face, width=width, two_lines=two_lines,
    )


def fit_name(
    *,
    text: str,
    face: Face,
    width: float,
    naming: Naming,
    two_lines: bool = False,
) -> tuple[str, ...]:
    """
    Fit a stop name to a slot.

    Parameters
    ----------
    text : str
        The stop name.
    face : Face
        Font.
    width : float
        Available width in pixels.
    naming : Naming
        Shortening rules and sibling names.
    two_lines : bool
        Whether the name may wrap onto two lines.

    Returns
    -------
    tuple[str, ...]
        One line, or two when wrapping is needed.
    """
    forms = name_forms(
        name=text, rules=naming.rules, siblings=naming.siblings,
    )
    return fit_forms(
        forms=forms, face=face, width=width, two_lines=two_lines,
    )


def fit_headsign(
    *,
    text: str,
    face: Face,
    width: float,
    naming: Naming,
    two_lines: bool = False,
) -> tuple[str, ...]:
    """
    Fit a headsign to a slot.

    Parameters
    ----------
    text : str
        The headsign.
    face : Face
        Font.
    width : float
        Available width in pixels.
    naming : Naming
        Shortening rules and sibling names.
    two_lines : bool
        Whether the headsign may wrap onto two lines.

    Returns
    -------
    tuple[str, ...]
        One line, or two when wrapping is needed.
    """
    forms = headsign_forms(
        text=text,
        rules=naming.rules,
        siblings=naming.siblings,
        one_line=not two_lines,
    )
    return fit_forms(
        forms=forms, face=face, width=width, two_lines=two_lines,
    )
