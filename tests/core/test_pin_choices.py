"""Tests for the choices a pin's editor accepts."""

from typing import Final

from paperroute_sydney.city import SYDNEY
from paperroute_transit.core.chooser import Choice
from paperroute_transit.core.pin_choices import (
    NICKNAME_MAX,
    busiest_line,
    clean_nickname,
    kind_names,
)

LINES: Final[tuple[Choice, ...]] = (
    Choice(key='train:GN:K', label='K', badge='GN', value='GN:K'),
    Choice(key='train:TL:B', label='B', badge='TL', value='TL:B'),
    Choice(key='train:GA:L', label='L', badge='GA', value='GA:L'),
)


def test_clean_nickname() -> None:
    """Whitespace is collapsed, blank is None and long is cut."""
    assert clean_nickname(text='  Work  trip ') == 'Work trip'
    assert clean_nickname(text='   ') is None
    assert clean_nickname(text='a' * 30) == 'a' * NICKNAME_MAX


def test_editor_kinds_from_specs() -> None:
    """The editor lists each top mode, then the modes under it."""
    assert list(kind_names(modes=SYDNEY.modes).values()) == [
        'Bus', 'Replacement buses', 'Train', 'Metro', 'Light rail', 'Ferry',
        'School', 'Coach',
    ]


def test_busiest_line_has_most_trips() -> None:
    """The line with most trips wins."""
    assert busiest_line(lines=LINES, trips={'TL:B': 3, 'GA:L': 2}) == LINES[1]


def test_busiest_line_tie_goes_to_the_first_listed() -> None:
    """Equal trips go to the line listed first."""
    assert busiest_line(lines=LINES, trips={'GA:L': 2, 'TL:B': 2}) == LINES[1]


def test_busiest_line_without_trips_is_the_first() -> None:
    """No trips at all gives the first line; no lines gives None."""
    assert busiest_line(lines=LINES, trips={}) == LINES[0]
    assert busiest_line(lines=(), trips={'GA:L': 1}) is None
