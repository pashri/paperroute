"""Tests for the shorter forms a stop name may take."""

from typing import Final

from paperroute_device.core.text import PLANE
from paperroute_transit.core.names import (
    JOINING_WORDS,
    NameRules,
    base_name,
    name_forms,
    without_platform,
)

PLAIN: Final[NameRules] = NameRules()
JOINED: Final[NameRules] = NameRules(joining=JOINING_WORDS)


def test_without_platform_drops_a_trailing_platform() -> None:
    """A trailing platform goes; a platform word elsewhere stays."""
    assert without_platform(name='Central Station Platform 16') == (
        'Central Station'
    )
    assert without_platform(name='Strathfield Station, Platform 3') == (
        'Strathfield Station'
    )
    assert without_platform(name='Platform Road') == 'Platform Road'


def test_base_name_keeps_a_bare_bus_station() -> None:
    """A stop named just 'Bus Station' keeps its suffix."""
    assert base_name(name='Bus Station') == 'Bus Station'


def test_base_name_drops_the_platform_and_a_trailing_station() -> None:
    """The base name has no platform and no trailing Station."""
    assert base_name(name='Central Station Platform 16') == 'Central'
    assert base_name(name='Town Hall Station') == 'Town Hall'
    assert base_name(name='Station Street') == 'Station Street'


def test_name_forms_shortens_one_word_at_a_time_from_the_end() -> None:
    """Each form shortens one more word, last word first."""
    assert name_forms(
        name='Westbrook Road Opp Cawdor Road', rules=JOINED,
    ) == (
        'Westbrook Road Opp Cawdor Road',
        'Westbrook Road Opp Cawdor Rd',
        'Westbrook Rd Opp Cawdor Rd',
        'Westbrook Rd opp Cawdor Rd',
        'Westbrook Rd/Cawdor Rd',
    )


def test_name_forms_keeps_joining_words_without_a_joining_list() -> None:
    """Without joining words the joining words stay as written."""
    assert name_forms(
        name='Westbrook Road Opp Cawdor Road', rules=PLAIN,
    ) == (
        'Westbrook Road Opp Cawdor Road',
        'Westbrook Road Opp Cawdor Rd',
        'Westbrook Rd Opp Cawdor Rd',
    )
    assert name_forms(name='Hills Road Near Addenbrookes', rules=PLAIN) == (
        'Hills Road Near Addenbrookes', 'Hills Rd Near Addenbrookes',
    )


def test_name_forms_leaves_a_street_word_that_does_not_end_a_street(
) -> None:
    """A street word not ending a street is not shortened."""
    assert name_forms(name='Newmarket Road Park and Ride', rules=PLAIN) == (
        'Newmarket Road Park and Ride', 'Newmarket Road P&R',
    )
    assert name_forms(name='Road End', rules=PLAIN) == ('Road End',)
    assert name_forms(name='Ash Grove', rules=PLAIN) == ('Ash Grove',)


def test_name_forms_reads_a_bracket_as_a_street_end() -> None:
    """A street word before a closing bracket is shortened."""
    assert name_forms(
        name='Rhodes Shopping Centre (Rider Boulevard)', rules=JOINED,
    ) == (
        'Rhodes Shopping Centre (Rider Boulevard)',
        'Rhodes Shopping Centre (Rider Bvd)',
        'Rhodes Shopping Ctr (Rider Bvd)',
    )


def test_name_forms_turns_airport_into_the_plane() -> None:
    """Airport becomes the aeroplane character."""
    assert name_forms(name='Heathrow Airport Terminal 5', rules=PLAIN) == (
        'Heathrow Airport Terminal 5',
        f'Heathrow {PLANE} Terminal 5',
        f'Heathrow {PLANE} Term 5',
    )


def test_name_forms_takes_the_longest_facility_words() -> None:
    """Longer facility words win over the shorter ones inside them."""
    assert name_forms(name='Ermington Public School', rules=PLAIN)[-1] == (
        'Ermington PS'
    )
    assert name_forms(
        name='Drummer Street Bus Station Bay 12', rules=PLAIN,
    )[-1] == 'Drummer Street Bus Stn Bay 12'
    assert name_forms(name='Parramatta Station, Stand A', rules=JOINED) == (
        'Parramatta Station, Stand A', 'Parramatta Stn, Stand A',
    )


def test_name_forms_keeps_bus_stn() -> None:
    """A name never shortens to end in a bare Bus."""
    forms = name_forms(name='Newmarket Bus Station', rules=PLAIN)
    assert forms == ('Newmarket Bus Station', 'Newmarket Bus Stn')
    assert not any(form.endswith(' Bus') for form in forms)


def test_name_forms_shortens_railway_names_too() -> None:
    """Street words in railway station names are shortened."""
    assert name_forms(name='London Liverpool Street', rules=PLAIN) == (
        'London Liverpool Street', 'London Liverpool St',
    )


def test_name_forms_starts_without_the_platform() -> None:
    """The first form has no platform; the next has no Station."""
    assert name_forms(name='Central Station Platform 16', rules=JOINED) == (
        'Central Station', 'Central',
    )


def test_name_forms_withholds_the_slash_from_an_alike_sibling() -> None:
    """The slash is skipped when a sibling would collapse to the same."""
    name = 'Victoria Rd At Church St'
    assert name_forms(
        name=name, rules=JOINED, siblings=('Victoria Rd Opp Church St',),
    ) == (name, 'Victoria Rd at Church St')
    assert name_forms(name=name, rules=JOINED, siblings=(name,))[-1] == (
        'Victoria Rd/Church St'
    )


def test_name_forms_of_an_empty_name() -> None:
    """An empty name has one empty form."""
    assert name_forms(name='', rules=JOINED) == ('',)
