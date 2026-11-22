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


def street_of(*, name: str, rules: NameRules) -> str | None:
    """
    Give the short street that comes before a joining word.

    Parameters
    ----------
    name : str
        The stop name.
    rules : NameRules
        The rules.

    Returns
    -------
    str or None
        The shortest form of the street, or None without a joining word.
    """
    match = JOINED_AT.match(base_name(name=name))
    if match is None:
        return None
    return name_forms(name=match[1], rules=rules)[-1]


def cross_street(*, name: str, rules: NameRules) -> str | None:
    """
    Give the joining word and the text after it.

    The joining word takes its short form when the rules list it.

    Parameters
    ----------
    name : str
        The stop name.
    rules : NameRules
        The rules.

    Returns
    -------
    str or None
        The cross street, or None without a joining word.
    """
    match = JOINED_AT.match(base_name(name=name))
    if match is None:
        return None
    word = match[2]
    for long, short in rules.joining:
        if word.lower() in (long.lower(), short.lower()):
            word = short
            break
    return f'{word} {match[3]}'
