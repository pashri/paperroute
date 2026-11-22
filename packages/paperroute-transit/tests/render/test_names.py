"""Tests for fitting a name to a slot."""

from typing import Final

from paperroute_device.core.text import PLANE
from paperroute_device.render.canvas import ELLIPSIS, Face, text_width
from paperroute_transit.core.names import JOINING_WORDS, NameRules
from paperroute_transit.render.names import (
    Naming,
    fit_forms,
    fit_headsign,
    fit_name,
)

FACE: Final[Face] = Face(weight=700, px=18)
SMALL: Final[Face] = Face(weight=400, px=14)
PLAIN_NAMING: Final[Naming] = Naming(rules=NameRules())
JOINED_NAMING: Final[Naming] = Naming(
    rules=NameRules(joining=JOINING_WORDS),
)
LONG: Final[str] = (
    'Cottenham Rampton Road via Histon Impington Village College'
)


def test_fit_name_keeps_a_name_that_fits() -> None:
    """A name that fits is returned unchanged on one line."""
    assert fit_name(
        text='Town Hall', face=FACE, width=200.0, naming=PLAIN_NAMING,
    ) == ('Town Hall',)


def test_fit_name_takes_the_first_form_that_fits() -> None:
    """The longest form that fits the width is chosen."""
    width = text_width(text='Westbrook Rd opp Cawdor Rd', face=FACE) + 0.5
    assert fit_name(
        text='Westbrook Road Opp Cawdor Road', face=FACE, width=width,
        naming=JOINED_NAMING,
    ) == ('Westbrook Rd opp Cawdor Rd',)


def test_fit_name_measures_the_plane() -> None:
    """The aeroplane character is measured as drawn."""
    form = f'Heathrow {PLANE} Terminal 5'
    width = text_width(text=form, face=FACE) + 0.5
    assert fit_name(
        text='Heathrow Airport Terminal 5', face=FACE, width=width,
        naming=PLAIN_NAMING,
    ) == (form,)


def test_fit_name_cuts_the_last_form_when_none_fits() -> None:
    """The shortest form is cut with an ellipsis when nothing fits."""
    (shown,) = fit_name(
        text='Westbrook Road Opp Cawdor Road', face=FACE, width=60.0,
        naming=JOINED_NAMING,
    )
    assert shown.endswith(ELLIPSIS)
    assert 'Westbrook Rd/Cawdor Rd'.startswith(shown.removesuffix(ELLIPSIS))
    assert text_width(text=shown, face=FACE) <= 60.0


def test_fit_name_of_an_empty_name() -> None:
    """An empty name gives one empty line."""
    assert fit_name(
        text='', face=FACE, width=100.0, naming=JOINED_NAMING,
    ) == ('',)
    assert fit_headsign(
        text='', face=FACE, width=100.0, naming=JOINED_NAMING,
        two_lines=True,
    ) == ('',)


def test_fit_headsign_wraps_whole_before_shortening() -> None:
    """Two lines are tried on the whole headsign first."""
    width = text_width(
        text='Cottenham Rampton Road via Histon', face=FACE,
    ) + 1
    assert fit_headsign(
        text=LONG, face=FACE, width=width, naming=PLAIN_NAMING,
        two_lines=True,
    ) == ('Cottenham Rampton Road via Histon', 'Impington Village College')


def test_fit_headsign_cuts_via_on_one_line() -> None:
    """On one line the via part is dropped."""
    assert fit_headsign(
        text=LONG, face=FACE, width=250.0, naming=PLAIN_NAMING,
    ) == ('Cottenham Rampton Road',)


def test_fit_forms_wraps_at_the_last_space_that_fits() -> None:
    """The first line takes as many words as fit."""
    width = text_width(text='London Kings', face=SMALL) + 1
    assert fit_forms(
        forms=('London Kings Cross',), face=SMALL, width=width,
        two_lines=True,
    ) == ('London Kings', 'Cross')


def test_fit_forms_cuts_the_second_line_when_two_are_not_enough() -> None:
    """Text beyond two lines ends in an ellipsis on the second."""
    width = text_width(text='Mount Victoria', face=SMALL) + 1
    first, second = fit_forms(
        forms=('Mount Victoria Via Parramatta And Penrith',), face=SMALL,
        width=width, two_lines=True,
    )
    assert first == 'Mount Victoria'
    assert second.endswith(ELLIPSIS)
    assert text_width(text=second, face=SMALL) <= width


def test_fit_forms_gives_one_cut_line_for_one_long_word() -> None:
    """A single long word is cut on one line."""
    (shown,) = fit_forms(
        forms=('Llanfairpwllgwyngyll',), face=SMALL, width=40.0,
        two_lines=True,
    )
    assert shown.endswith(ELLIPSIS)
