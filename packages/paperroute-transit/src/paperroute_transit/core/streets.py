"""The street and the cross street of a stop name."""

import re
from typing import Final

from paperroute_transit.core.names import (
    JOINING_WORDS,
    NameRules,
    alternatives,
    base_name,
    name_forms,
)

JOINERS: Final[list[str]] = sorted(
    {word for pair in JOINING_WORDS for word in pair},
    key=len,
    reverse=True,
)
JOINED_AT: Final[re.Pattern[str]] = re.compile(
    f'^(.+?) ((?i:{alternatives(words=JOINERS)})) (.+)$',
)


BUSWAY: Final[str] = 'The Busway'


def joiner(*, word: str, rules: NameRules) -> str:
    """
    Give a joining word in its short form when the rules list it.

    Parameters
    ----------
    word : str
        The joining word as written.
    rules : NameRules
        The rules.

    Returns
    -------
    str
        The short form, or the word as written.
    """
    for long, short in rules.joining:
        if word.lower() in (long.lower(), short.lower()):
            return short
    return word


def street_parts(*, name: str, rules: NameRules) -> tuple[str, str] | None:
    """
    Split a stop name into its street and its own part.

    The forms read are 'Street joiner Cross', 'The Busway X' and
    'X, Street'; a joining word wins over the other two.

    Parameters
    ----------
    name : str
        The stop name.
    rules : NameRules
        The rules.

    Returns
    -------
    tuple[str, str] or None
        The shortest form of the street and the rest of the name, or
        None when the name has no street.
    """
    text = base_name(name=name)
    match = JOINED_AT.match(text)
    if match is not None:
        return (
            name_forms(name=match[1], rules=rules)[-1],
            f'{joiner(word=match[2], rules=rules)} {match[3]}',
        )
    if text.startswith(f'{BUSWAY} '):
        return BUSWAY, text.removeprefix(f'{BUSWAY} ')
    if ', ' in text:
        cross, street = text.rsplit(', ', 1)
        return name_forms(name=street, rules=rules)[-1], cross
    return None


def street_of(*, name: str, rules: NameRules) -> str | None:
    """
    Give the short street a stop name is on.

    Parameters
    ----------
    name : str
        The stop name.
    rules : NameRules
        The rules.

    Returns
    -------
    str or None
        The shortest form of the street, or None when the name has no
        street.
    """
    parts = street_parts(name=name, rules=rules)
    return None if parts is None else parts[0]


def cross_street(*, name: str, rules: NameRules) -> str | None:
    """
    Give the part of a stop name after its street.

    A joining word takes its short form when the rules list it.

    Parameters
    ----------
    name : str
        The stop name.
    rules : NameRules
        The rules.

    Returns
    -------
    str or None
        The rest of the name, or None when the name has no street.
    """
    parts = street_parts(name=name, rules=rules)
    return None if parts is None else parts[1]
