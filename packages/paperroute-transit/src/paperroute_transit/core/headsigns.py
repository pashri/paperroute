"""Tidy headsigns and list their shorter forms, by string rules alone."""

import html
import re
from collections.abc import Sequence
from typing import Final

from paperroute_transit.core.names import NameRules, base_name, step_forms

VIA: Final[re.Pattern[str]] = re.compile(r'\s+[Vv]ia\s.*$')
SPACES: Final[re.Pattern[str]] = re.compile(r'\s+')
COMMA_GAP: Final[re.Pattern[str]] = re.compile(r',(?=\S)')
WORD: Final[re.Pattern[str]] = re.compile(r"[\w']+")
DASH: Final[str] = ' - '
TAIL_SEPARATOR: Final[str] = ', '


def tidy_headsign(*, text: str, rules: NameRules) -> str:
    """
    Decode, respace and trim a headsign.

    Parameters
    ----------
    text : str
        The headsign as published.
    rules : NameRules
        The rules; with the dash rule only the part after the last dash
        is kept.

    Returns
    -------
    str
        The tidied headsign, without a trailing Station.
    """
    text = SPACES.sub(' ', html.unescape(text)).strip()
    text = COMMA_GAP.sub(', ', text)
    if rules.dash:
        text = text.rsplit(DASH, 1)[-1]
    return base_name(name=text)


def without_tail(*, text: str, rules: NameRules) -> str:
    """
    Drop a trailing part that repeats the head or names a centre.

    Parameters
    ----------
    text : str
        The tidied headsign.
    rules : NameRules
        The rules, whose centres name the droppable tails.

    Returns
    -------
    str
        The headsign without its tail, or unchanged.
    """
    head, separator, tail = text.rpartition(TAIL_SEPARATOR)
    if not separator:
        return text
    head_words = {word.lower() for word in WORD.findall(head)}
    tail_words = [word.lower() for word in WORD.findall(tail)]
    repeats = all(word in head_words for word in tail_words)
    return head if tail in rules.centres or repeats else text


def without_via(*, text: str) -> str:
    """
    Cut a via and everything after it.

    Parameters
    ----------
    text : str
        The headsign.

    Returns
    -------
    str
        The headsign before a joining via, or unchanged.
    """
    return VIA.sub('', text)


def headsign_forms(
    *,
    text: str,
    rules: NameRules,
    siblings: Sequence[str] = (),
    one_line: bool = True,
) -> tuple[str, ...]:
    """
    List the forms of a headsign, from the longest to the shortest.

    Parameters
    ----------
    text : str
        The tidied headsign.
    rules : NameRules
        The rules.
    siblings : sequence of str
        Other headsigns shown alongside.
    one_line : bool
        Whether the headsign may lose its via; False keeps it.

    Returns
    -------
    tuple of str
        The forms without duplicates.
    """
    others = [sibling for sibling in siblings if sibling != text]
    forms = [text, without_tail(text=text, rules=rules)]
    cut = without_via(text=forms[-1])
    alike = any(without_via(text=other) == cut for other in others)
    if one_line and not alike:
        forms.append(cut)
    forms += step_forms(text=forms[-1], rules=rules, siblings=others)
    return tuple(dict.fromkeys(forms))
