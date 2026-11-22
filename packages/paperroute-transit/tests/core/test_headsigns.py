"""Tests for tidying headsigns and listing their shorter forms."""

from typing import Final

from paperroute_device.core.text import PLANE
from paperroute_transit.core.headsigns import (
    headsign_forms,
    tidy_headsign,
    without_tail,
    without_via,
)
from paperroute_transit.core.names import JOINING_WORDS, NameRules

PLAIN: Final[NameRules] = NameRules()
JOINED: Final[NameRules] = NameRules(joining=JOINING_WORDS)
DASHED: Final[NameRules] = NameRules(dash=True)
LONG: Final[str] = (
    'Cottenham Rampton Road via Histon Impington Village College'
)


def test_tidy_headsign_decodes_and_spaces() -> None:
    """Entities decode, spacing is fixed and a trailing Station goes."""
    assert tidy_headsign(text='Addenbrooke&#39;s  Hospital', rules=PLAIN) == (
        "Addenbrooke's Hospital"
    )
    assert tidy_headsign(text='Cambridge,Drummer St', rules=PLAIN) == (
        'Cambridge, Drummer St'
    )
    assert tidy_headsign(text=' Penrith Station ', rules=PLAIN) == 'Penrith'


def test_tidy_headsign_keeps_the_part_after_the_dash_only_when_asked(
) -> None:
    """The dash splits a headsign only with the dash rule."""
    text = 'Acle - Heathrow Airport Terminal 5, Heathrow Airport'
    assert tidy_headsign(text=text, rules=DASHED) == (
        'Heathrow Airport Terminal 5, Heathrow Airport'
    )
    assert tidy_headsign(text='Hornsby - Berowra', rules=PLAIN) == (
        'Hornsby - Berowra'
    )


def test_without_tail_drops_a_repeat_or_a_known_centre() -> None:
    """A tail that repeats the head or names a centre is dropped."""
    assert without_tail(
        text='Heathrow Airport Terminal 5, Heathrow Airport', rules=PLAIN,
    ) == 'Heathrow Airport Terminal 5'
    centres = NameRules(centres=('London',))
    assert without_tail(text='St Margarets, London', rules=centres) == (
        'St Margarets'
    )
    assert without_tail(
        text='Cambridge, Drummer Street D3', rules=centres,
    ) == 'Cambridge, Drummer Street D3'


def test_without_via_cuts_only_a_joining_via() -> None:
    """Only a via between words is cut."""
    assert without_via(text=LONG) == 'Cottenham Rampton Road'
    assert without_via(text='Parramatta Via Granville') == 'Parramatta'
    assert without_via(text='Pyrmont Viaduct') == 'Pyrmont Viaduct'
    assert without_via(text='Via Parramatta') == 'Via Parramatta'


def test_headsign_forms_cuts_the_tail_before_the_words() -> None:
    """The via and tail cuts come before word shortening."""
    assert headsign_forms(text=LONG, rules=PLAIN) == (
        LONG, 'Cottenham Rampton Road', 'Cottenham Rampton Rd',
    )
    assert headsign_forms(
        text='Heathrow Airport Terminal 5, Heathrow Airport', rules=PLAIN,
    )[1] == 'Heathrow Airport Terminal 5'


def test_headsign_forms_keeps_via_on_two_lines() -> None:
    """With two lines the via stays and only words shorten."""
    assert headsign_forms(text=LONG, rules=PLAIN, one_line=False) == (
        LONG, 'Cottenham Rampton Road via Histon Impington Village Coll',
    )
    assert headsign_forms(
        text='City Circle via Airport', rules=JOINED, one_line=False,
    ) == ('City Circle via Airport', f'City Circle via {PLANE}')


def test_headsign_forms_keeps_via_when_a_sibling_would_read_alike() -> None:
    """A sibling with the same cut form keeps the via."""
    assert headsign_forms(
        text='Mount Druitt via Plumpton',
        rules=JOINED,
        siblings=('Mount Druitt via Rooty Hill',),
    ) == ('Mount Druitt via Plumpton',)


def test_headsign_forms_cuts_via_beside_its_own_repeat() -> None:
    """A sibling equal to the text does not keep the via."""
    text = 'Mount Druitt via Rooty Hill'
    assert headsign_forms(text=text, rules=JOINED, siblings=(text, text)) == (
        text, 'Mount Druitt',
    )
