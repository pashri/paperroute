"""Tests for the choices a pin's editor accepts."""

from paperroute.city import SYDNEY
from paperroute.core.pin_choices import (
    NICKNAME_MAX,
    clean_nickname,
    kind_names,
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
