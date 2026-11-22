"""Tests for splitting a stop name into street and cross street."""

from typing import Final

from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.core.streets import cross_street, street_of

PLAIN: Final[NameRules] = NameRules()
JOINED: Final[NameRules] = NameRules(joining=JOINING_WORDS)
UNI: Final[str] = 'Parramatta Road Opp University Of Sydney'


def test_street_of_is_the_short_street_before_a_joining_word() -> None:
    """The street is the short form of the text before the joiner."""
    assert street_of(name=UNI, rules=JOINED) == 'Parramatta Rd'
    assert street_of(
        name='Hills Road opp Cambridge Leisure', rules=PLAIN,
    ) == 'Hills Rd'


def test_street_of_needs_a_joining_word_after_the_first() -> None:
    """Names without a joining word after the first word have no street."""
    assert street_of(name='Histon Road Gilbert Road', rules=PLAIN) is None
    assert street_of(name='Opp Town Hall', rules=JOINED) is None
    assert street_of(name='Leichhardt PS, Norton St', rules=JOINED) is None
    assert street_of(name='Central Station Platform 3', rules=JOINED) is None


def test_cross_street_shortens_the_joining_word_where_the_city_does(
) -> None:
    """The joiner takes its short form only when the rules list it."""
    assert cross_street(name=UNI, rules=JOINED) == 'opp University Of Sydney'
    assert cross_street(name=UNI, rules=PLAIN) == 'Opp University Of Sydney'
    assert cross_street(
        name='Parramatta Rd Bef Lyons Rd', rules=JOINED,
    ) == 'bef Lyons Rd'
    assert cross_street(name='Histon Road Gilbert Road', rules=PLAIN) is None
