"""Shorter forms of a stop name, made by string rules alone."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final

from paperroute_device.core.text import PLANE

Pairs = tuple[tuple[str, str], ...]

STREET_WORDS: Final[Pairs] = (
    ('Road', 'Rd'),
    ('Street', 'St'),
    ('Avenue', 'Av'),
    ('Close', 'Cl'),
    ('Lane', 'Ln'),
    ('Drive', 'Dr'),
    ('Parade', 'Pde'),
    ('Highway', 'Hwy'),
    ('Crescent', 'Cres'),
    ('Place', 'Pl'),
    ('Court', 'Ct'),
    ('Circuit', 'Cct'),
    ('Boulevard', 'Bvd'),
    ('Terrace', 'Tce'),
    ('Gardens', 'Gdns'),
    ('Square', 'Sq'),
    ('Esplanade', 'Esp'),
    ('Parkway', 'Pkwy'),
    ('Motorway', 'Mwy'),
    ('Walk', 'Wk'),
)
FACILITY_WORDS: Final[Pairs] = (
    ('Public School', 'PS'),
    ('Bus Station', 'Bus Stn'),
    ('Station', 'Stn'),
    ('Interchange', 'Intch'),
    ('Wharf', 'Whf'),
    ('Light Rail', 'LR'),
    ('Terminal', 'Term'),
    ('Park and Ride', 'P&R'),
    ('Park And Ride', 'P&R'),
    ('Park & Ride', 'P&R'),
    ('Park-and-Ride', 'P&R'),
    ('Hospital', 'Hosp'),
    ('University', 'Univ'),
    ('College', 'Coll'),
    ('School', 'Sch'),
    ('Centre', 'Ctr'),
)
JOINING_WORDS: Final[Pairs] = (
    ('At', 'at'),
    ('Opp', 'opp'),
    ('After', 'aft'),
    ('Before', 'bef'),
    ('Near', 'nr'),
)
PLATFORM: Final[re.Pattern[str]] = re.compile(r',? Platform \S+$')
STATION_SUFFIX: Final[str] = ' Station'
BUS_END: Final[str] = ' Bus'


@dataclass(frozen=True)
class NameRules:
    """
    Word lists and options that decide how a stop name shortens.

    Attributes
    ----------
    street : tuple of tuple of str
        Street words as (long, short) pairs.
    facility : tuple of tuple of str
        Facility words as (long, short) pairs.
    joining : tuple of tuple of str
        Joining words as (long, short) pairs; empty keeps them as written.
    centres : tuple of str
        Names of city centres.
    dash : bool
        Whether a dash may split a name.
    """

    street: Pairs = STREET_WORDS
    facility: Pairs = FACILITY_WORDS
    joining: Pairs = ()
    centres: tuple[str, ...] = ()
    dash: bool = False


@dataclass(frozen=True)
class RulePatterns:
    """
    Compiled patterns for one set of name rules.

    Attributes
    ----------
    airport : re.Pattern
        Matches the word Airport.
    street : re.Pattern
        Matches a street word that ends a street.
    facility : re.Pattern
        Matches a facility word.
    joining : re.Pattern or None
        Matches a long joining word; None without joining words.
    slash : re.Pattern or None
        Matches a short joining word between spaces; None without them.
    """

    airport: re.Pattern[str]
    street: re.Pattern[str]
    facility: re.Pattern[str]
    joining: re.Pattern[str] | None
    slash: re.Pattern[str] | None


def alternatives(*, words: Sequence[str]) -> str:
    """
    Join words into one escaped regular expression alternation.

    Parameters
    ----------
    words : sequence of str
        The words, in match order.

    Returns
    -------
    str
        The alternation, without surrounding brackets.
    """
    return '|'.join(re.escape(word) for word in words)


@cache
def rule_patterns(*, rules: NameRules) -> RulePatterns:
    """
    Compile the patterns for a set of name rules.

    Parameters
    ----------
    rules : NameRules
        The rules.

    Returns
    -------
    RulePatterns
        The compiled patterns.
    """
    joiners = [word for pair in JOINING_WORDS for word in pair]
    ends = (
        f' (?:{alternatives(words=joiners)}) |,| ?\\(|\\)|$'
    )
    street = alternatives(words=[long for long, _ in rules.street])
    facility = alternatives(words=[long for long, _ in rules.facility])
    return RulePatterns(
        airport=re.compile(r'\bAirport\b'),
        street=re.compile(f'(?<=\\S )(?:{street})(?={ends})'),
        facility=re.compile(f'\\b(?:{facility})\\b'),
        joining=joining_pattern(rules=rules, index=0),
        slash=joining_pattern(rules=rules, index=1),
    )


def joining_pattern(
    *,
    rules: NameRules,
    index: int,
) -> re.Pattern[str] | None:
    """
    Compile the joining-word pattern for the long or short forms.

    Parameters
    ----------
    rules : NameRules
        The rules.
    index : int
        0 for the long forms between spaces kept, 1 for the short forms
        with their spaces matched.

    Returns
    -------
    re.Pattern or None
        The pattern, or None without joining words.
    """
    if not rules.joining:
        return None
    words = alternatives(words=[pair[index] for pair in rules.joining])
    if index == 0:
        return re.compile(f'(?<= )(?:{words})(?= )')
    return re.compile(f' (?:{words}) ')


def without_platform(*, name: str) -> str:
    """
    Drop a trailing platform from a name.

    Parameters
    ----------
    name : str
        The stop name.

    Returns
    -------
    str
        The name without a trailing platform.
    """
    return PLATFORM.sub('', name)


def base_name(*, name: str) -> str:
    """
    Drop a trailing platform and a trailing Station from a name.

    A name that would be left ending in a bare Bus keeps its Station.

    Parameters
    ----------
    name : str
        The stop name.

    Returns
    -------
    str
        The base name.
    """
    text = without_platform(name=name)
    if not text.endswith(STATION_SUFFIX):
        return text
    stem = text[: -len(STATION_SUFFIX)]
    bus = stem == BUS_END.strip() or stem.endswith(BUS_END)
    return text if bus else stem


def from_end(
    *,
    text: str,
    pattern: re.Pattern[str],
    table: Mapping[str, str],
) -> list[str]:
    """
    Replace matches one at a time, last match first.

    Parameters
    ----------
    text : str
        The text to shorten.
    pattern : re.Pattern
        Matches the words to replace.
    table : mapping of str to str
        Replacement for each stripped match.

    Returns
    -------
    list of str
        The text after each replacement, each keeping the earlier ones.
    """
    forms: list[str] = []
    for match in reversed(list(pattern.finditer(text))):
        text = (
            text[: match.start()]
            + table[match[0].strip()]
            + text[match.end():]
        )
        forms.append(text)
    return forms


@cache
def slashed(*, text: str, rules: NameRules) -> str:
    """
    Give the last form of a text with every slash applied.

    Parameters
    ----------
    text : str
        The text to shorten.
    rules : NameRules
        The rules.

    Returns
    -------
    str
        The shortest form.
    """
    return step_forms(text=text, rules=rules)[-1]


def step_forms(
    *,
    text: str,
    rules: NameRules,
    siblings: Sequence[str] = (),
) -> tuple[str, ...]:
    """
    List the forms of a text, from the longest to the shortest.

    The slash form is left out when a differing sibling would shorten
    to the same slashed text.

    Parameters
    ----------
    text : str
        The text to shorten.
    rules : NameRules
        The rules.
    siblings : sequence of str
        Other stop names shown alongside.

    Returns
    -------
    tuple of str
        The forms without duplicates.
    """
    patterns = rule_patterns(rules=rules)
    forms = [text]
    steps: list[tuple[re.Pattern[str] | None, Mapping[str, str]]] = [
        (patterns.airport, {'Airport': PLANE}),
        (patterns.street, dict(rules.street)),
        (patterns.facility, dict(rules.facility)),
        (patterns.joining, dict(rules.joining)),
    ]
    if not clashes(text=text, rules=rules, siblings=siblings):
        steps.append(
            (patterns.slash, {short: '/' for _, short in rules.joining}),
        )
    for pattern, table in steps:
        if pattern is not None:
            forms += from_end(text=forms[-1], pattern=pattern, table=table)
    return tuple(dict.fromkeys(forms))


def clashes(*, text: str, rules: NameRules, siblings: Sequence[str]) -> bool:
    """
    Tell whether a differing sibling slashes to the same text.

    Parameters
    ----------
    text : str
        The text to shorten.
    rules : NameRules
        The rules.
    siblings : sequence of str
        Other stop names shown alongside.

    Returns
    -------
    bool
        True when the slash form would be ambiguous.
    """
    if not siblings:
        return False
    own = slashed(text=text, rules=rules)
    return any(
        base_name(name=sibling) != text
        and slashed(text=sibling, rules=rules) == own
        for sibling in siblings
    )


def name_forms(
    *,
    name: str,
    rules: NameRules,
    siblings: Sequence[str] = (),
) -> tuple[str, ...]:
    """
    List the forms of a stop name, from the longest to the shortest.

    Parameters
    ----------
    name : str
        The stop name.
    rules : NameRules
        The rules.
    siblings : sequence of str
        Other stop names shown alongside.

    Returns
    -------
    tuple of str
        The forms without duplicates.
    """
    base = base_name(name=name)
    return tuple(
        dict.fromkeys(
            [
                without_platform(name=name),
                *step_forms(text=base, rules=rules, siblings=siblings),
            ],
        ),
    )
