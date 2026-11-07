"""Stylesheets read as flat rule lists."""

from typing import Final

import pytest

from tests.css_rules import CssRule, css_rules, rule_line

SHEET: Final[str] = """
/* a comment { with braces } */
@font-face {
  font-family: 'Sans';
  src: url('/sans.woff2') format('woff2');
}

main  >  p {
  color:   red;
  margin: 0
}

@media (min-width: 48em) {
  a {x: 1}
  b,
  c {y: 2}
}
"""
WIDE: Final[str] = '@media (min-width: 48em)'


def test_css_rules_reads_media_and_font_face() -> None:
    """Rules come in order, with their `@media` context."""
    assert css_rules(css=SHEET) == [
        CssRule(
            context='',
            selector='@font-face',
            body=(
                "font-family: 'Sans'",
                "src: url('/sans.woff2') format('woff2')",
            ),
        ),
        CssRule(
            context='', selector='main > p', body=('color: red', 'margin: 0'),
        ),
        CssRule(context=WIDE, selector='a', body=('x: 1',)),
        CssRule(context=WIDE, selector='b, c', body=('y: 2',)),
    ]


def test_css_rules_refuses_an_unclosed_block() -> None:
    """A block that never closes is an error."""
    with pytest.raises(ValueError, match='unclosed'):
        css_rules(css='a { color: red;')


def test_rule_line_joins_context_selector_and_body() -> None:
    """A rule is one line, its parts separated by bars."""
    rule = CssRule(context=WIDE, selector='b, c', body=('x: 1', 'y: 2'))
    assert rule_line(rule=rule) == f'{WIDE} | b, c | x: 1; y: 2'
