"""Tests for the choices a pin's editor accepts."""

from paperroute.core.pin_choices import NICKNAME_MAX, clean_nickname


def test_clean_nickname() -> None:
    """Whitespace is collapsed, blank is None and long is cut."""
    assert clean_nickname(text='  Work  trip ') == 'Work trip'
    assert clean_nickname(text='   ') is None
    assert clean_nickname(text='a' * 30) == 'a' * NICKNAME_MAX
