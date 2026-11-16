"""Stylesheets read as flat lists of rules, for comparing them."""

import ast
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

COMMENT: Final[re.Pattern[str]] = re.compile(r'/\*.*?\*/', flags=re.DOTALL)
ATTRIBUTE: Final[re.Pattern[str]] = re.compile(
    r'(?<![\w-])(?:class|id)="([^"]*)"',
)
SCRIPT_CALL: Final[re.Pattern[str]] = re.compile(
    r"(?:classList\.\w+|getElementById)\(\s*'([\w\s,'-]*)'\s*\)",
)
QUERY: Final[re.Pattern[str]] = re.compile(
    r"querySelector(?:All)?\(\s*(['\"`])(.*?)\1",
)
SELECTOR_TOKEN: Final[re.Pattern[str]] = re.compile(
    r'[.#](-?[A-Za-z_][\w-]*)',
)
BRACKETED: Final[re.Pattern[str]] = re.compile(r'\[[^\]]*\]')


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


def top_level_split(*, text: str) -> list[str]:
    """
    Split a selector list at the commas outside brackets.

    Parameters
    ----------
    text : str
        A selector list.

    Returns
    -------
    list[str]
        Each selector, trimmed.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        depth += {'(': 1, '[': 1, ')': -1, ']': -1}.get(char, 0)
        if char == ',' and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return parts


def selector_tokens(*, selector: str) -> frozenset[str]:
    """
    List the class and id names a selector names.

    Attribute selectors are skipped, so a quoted value never counts.

    Parameters
    ----------
    selector : str
        One selector, or a selector list.

    Returns
    -------
    frozenset[str]
        The names, without their `.` or `#`.
    """
    return frozenset(
        SELECTOR_TOKEN.findall(BRACKETED.sub('', selector)),
    )


def python_strings(*, path: Path) -> Iterator[str]:
    """
    Yield the string literals of a Python file, docstrings left out.

    The literal parts of an f-string count; its fields do not.

    Parameters
    ----------
    path : Path
        A Python source file.

    Yields
    ------
    str
        Each string literal.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    yield from (
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def script_tokens(*, script: str) -> set[str]:
    """
    Read the class and id names a script puts on or looks up in markup.

    Parameters
    ----------
    script : str
        A JavaScript file's text.

    Returns
    -------
    set[str]
        Arguments of `classList.*` and `getElementById`, and the `.x`
        and `#x` names in `querySelector` strings.
    """
    called = {
        name.strip(" '")
        for arguments in SCRIPT_CALL.findall(script)
        for name in arguments.split(',')
    }
    queried = {
        name
        for _, query in QUERY.findall(script)
        for name in selector_tokens(selector=query)
    }
    return (called | queried) - {''}


def web_files(*, root: Traversable) -> Iterator[Traversable]:
    """
    Walk a web directory's files.

    Parameters
    ----------
    root : Traversable
        The directory.

    Yields
    ------
    Traversable
        Every file under it, at any depth.
    """
    for entry in root.iterdir():
        if entry.is_dir():
            yield from web_files(root=entry)
        else:
            yield entry


def markup_tokens(
    *, root: Traversable, python: Sequence[Path],
) -> frozenset[str]:
    """
    Read every class and id name that markup can carry.

    Names come only from markup: `class` and `id` values in the
    templates and in Python string literals, and the names scripts set
    or query. Prose and code words never count.

    Parameters
    ----------
    root : Traversable
        The web directory holding `.html` templates and `.js` scripts.
    python : Sequence[Path]
        Python modules that build markup in strings.

    Returns
    -------
    frozenset[str]
        The names.
    """
    texts = [
        (entry.name, entry.read_text(encoding='utf-8'))
        for entry in web_files(root=root)
        if entry.name.endswith(('.html', '.js'))
    ]
    strings = [
        text
        for path in python
        for text in python_strings(path=path)
    ]
    markup = [text for name, text in texts if name.endswith('.html')]
    return frozenset(
        name
        for text in [*markup, *strings]
        for value in ATTRIBUTE.findall(text)
        for name in value.split()
        if not {'{', '}'} & set(name)
    ) | frozenset(
        name
        for name_, text in texts
        if name_.endswith('.js')
        for name in script_tokens(script=text)
    )

