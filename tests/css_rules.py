"""Stylesheets read as flat lists of rules, for comparing them."""

import re
from dataclasses import dataclass, replace
from typing import Final

COMMENT: Final[re.Pattern[str]] = re.compile(r'/\*.*?\*/', flags=re.DOTALL)


@dataclass(frozen=True)
class CssRule:
    """
    One style rule and the `@media` block it sits in.

    Attributes
    ----------
    context : str
        The `@media` prelude, or '' at the top level.
    selector : str
        The selector list, or '@font-face'.
    body : tuple[str, ...]
        The declarations, in order.
    """

    context: str
    selector: str
    body: tuple[str, ...]


def collapsed(*, text: str) -> str:
    """
    Collapse every run of whitespace to one space.

    Parameters
    ----------
    text : str
        Any text.

    Returns
    -------
    str
        The text, trimmed, with single spaces.
    """
    return ' '.join(text.split())


def closing(*, text: str, start: int) -> int:
    """
    Find the brace that closes the block opened at `start`.

    Parameters
    ----------
    text : str
        The stylesheet.
    start : int
        Index of an opening brace.

    Returns
    -------
    int
        Index of its closing brace.
    """
    depth = 0
    for index in range(start, len(text)):
        depth += {'{': 1, '}': -1}.get(text[index], 0)
        if depth == 0:
            return index
    raise ValueError(f'unclosed block at {start}')


def css_rules(*, css: str) -> list[CssRule]:
    """
    Read a stylesheet's rules, one `@media` level deep.

    Parameters
    ----------
    css : str
        The stylesheet.

    Returns
    -------
    list[CssRule]
        Every rule in order; rules inside `@media` carry its prelude.
    """
    text = COMMENT.sub('', css)
    rules: list[CssRule] = []
    position = 0
    while (start := text.find('{', position)) != -1:
        prelude = collapsed(text=text[position:start])
        end = closing(text=text, start=start)
        if prelude.startswith('@media'):
            rules.extend(
                replace(rule, context=prelude)
                for rule in css_rules(css=text[start + 1:end])
            )
        else:
            rules.append(
                CssRule(
                    context='',
                    selector=prelude,
                    body=declarations(text=text[start + 1:end]),
                ),
            )
        position = end + 1
    return rules


def declarations(*, text: str) -> tuple[str, ...]:
    """
    Split a rule's body into its declarations.

    Parameters
    ----------
    text : str
        What sits between the rule's braces.

    Returns
    -------
    tuple[str, ...]
        Each declaration, collapsed, without its semicolon.
    """
    return tuple(
        line
        for part in text.split(';')
        if (line := collapsed(text=part))
    )


def rule_line(*, rule: CssRule) -> str:
    """
    Write a rule on one line.

    Parameters
    ----------
    rule : CssRule
        The rule.

    Returns
    -------
    str
        Context, selector and declarations, separated by ' | '.
    """
    return f'{rule.context} | {rule.selector} | {"; ".join(rule.body)}'
