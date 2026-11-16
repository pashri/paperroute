"""Tests for the Cambridge phone stylesheet."""

import re
from pathlib import Path
from typing import Final

import pytest

from paperroute_cambridge.city import CAMBRIDGE
from paperroute_device_testkit.css_rules import (
    CssRule,
    css_rules,
    selector_tokens,
    top_level_split,
)
from paperroute_device_testkit.paths import ROOT

LIVE_CSS: Final[str] = (
    ROOT / 'packages/paperroute-device/src/paperroute_device/web/live.css'
).read_text(encoding='utf-8')
BOARD_CSS: Final[str] = (
    ROOT / 'packages/paperroute-transit/src/paperroute_transit/web/board.css'
).read_text(encoding='utf-8')
PALETTE_CSS: Final[str] = (
    Path(__file__).parents[1]
    / 'src/paperroute_cambridge/assets/web/palette.css'
).read_text(encoding='utf-8')
DARK: Final[str] = '@media (prefers-color-scheme: dark)'
REFERENCE: Final[re.Pattern[str]] = re.compile(r'var\((--[\w-]+)\)')
PAINTED: Final[tuple[tuple[str, str], ...]] = (  # text token, token under it
    ('--text', '--page'), ('--text', '--surface'), ('--text', '--current-bg'),
    ('--muted', '--page'), ('--muted', '--surface'),
    ('--heading', '--page'), ('--blue', '--page'), ('--blue', '--surface'),
    ('--secondary', '--page'), ('--secondary', '--surface'),
    ('--secondary', '--current-bg'),
    ('--current', '--current-bg'), ('--main-text', '--main'),
    ('--sign-text', '--sign'), ('--ticket-text', '--ticket'),
    ('--danger', '--page'), ('--danger', '--surface'),
    ('--danger-text', '--danger'),
)
CHIP_TEXT: Final[str] = '#111111'
READABLE: Final[float] = 4.5
COACH_CHIP_LIGHT: Final[float] = 4.3  # the decided exception: flame red


def tokens(rule: CssRule) -> dict[str, str]:
    """
    Read a rule's custom property declarations.

    Parameters
    ----------
    rule : CssRule
        The rule.

    Returns
    -------
    dict[str, str]
        Each `--name` and its value.
    """
    return {
        name: value.strip()
        for name, _, value in (line.partition(':') for line in rule.body)
        if name.startswith('--')
    }


def tokens_and_properties(rule: CssRule) -> set[str]:
    """
    Read the names of every property a rule declares.

    Parameters
    ----------
    rule : CssRule
        The rule.

    Returns
    -------
    set[str]
        The property names.
    """
    return {line.partition(':')[0] for line in rule.body}


def root_rules(*, css: str, dark: bool) -> list[CssRule]:
    """
    Pick a sheet's `:root` rules, in the light or the dark context.

    Parameters
    ----------
    css : str
        The stylesheet.
    dark : bool
        True for the `prefers-color-scheme: dark` rules, else the
        top-level ones.

    Returns
    -------
    list[CssRule]
        The matching `:root` rules.
    """
    context = DARK if dark else ''
    return [
        rule
        for rule in css_rules(css=css)
        if rule.selector == ':root' and rule.context == context
    ]


def kit_dark_root() -> CssRule:
    """
    Find the kit stylesheet's dark `:root` rule.

    Returns
    -------
    CssRule
        The rule.
    """
    (rule,) = root_rules(css=LIVE_CSS, dark=True)
    return rule


def resolved(*, scheme: str) -> dict[str, str]:
    """
    Work out every token's final value in a colour scheme.

    Parameters
    ----------
    scheme : str
        'light' or 'dark'.

    Returns
    -------
    dict[str, str]
        Each token and its value with every `var()` followed.
    """
    layers = [(LIVE_CSS, False), (PALETTE_CSS, False)]
    if scheme == 'dark':
        layers = [
            (LIVE_CSS, False), (LIVE_CSS, True),
            (PALETTE_CSS, False), (PALETTE_CSS, True),
        ]
    values: dict[str, str] = {}
    for css, dark in layers:
        for rule in root_rules(css=css, dark=dark):
            values.update(tokens(rule))

    def follow(value: str) -> str:
        return REFERENCE.sub(lambda m: follow(values[m.group(1)]), value)

    return {name: follow(value) for name, value in values.items()}


def same_rule_pairs(css: str) -> set[tuple[str, str]]:
    """
    Find the colour and background tokens that one rule sets together.

    Parameters
    ----------
    css : str
        The stylesheet.

    Returns
    -------
    set[tuple[str, str]]
        Each (colour token, background token) pair, both given as
        `var()` references.
    """
    pairs: set[tuple[str, str]] = set()
    for rule in css_rules(css=css):
        values = {
            name: REFERENCE.fullmatch(value.strip())
            for name, _, value in (line.partition(':') for line in rule.body)
        }
        colour = values.get('color')
        ground = values.get('background') or values.get('background-color')
        if colour and ground:
            pairs.add((colour.group(1), ground.group(1)))
    return pairs


def class_names(css: str) -> set[str]:
    """
    List the class names in the selectors that start with a class.

    Parameters
    ----------
    css : str
        The stylesheet.

    Returns
    -------
    set[str]
        The names, without their dot.
    """
    return {
        name
        for rule in css_rules(css=css)
        for part in top_level_split(text=rule.selector)
        if part.startswith('.')
        for name in selector_tokens(selector=part)
    }


def luminance(colour: str) -> float:
    """
    Compute the relative luminance of a `#rrggbb` colour.

    Parameters
    ----------
    colour : str
        The colour.

    Returns
    -------
    float
        The WCAG relative luminance.
    """
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    red, green, blue = (
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    """
    Compute the WCAG contrast ratio of two `#rrggbb` colours.

    Parameters
    ----------
    first : str
        One colour.
    second : str
        The other colour.

    Returns
    -------
    float
        The ratio, from 1 to 21.
    """
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_every_painted_pair_reaches_the_contrast_ratio(scheme: str) -> None:
    """Every painted pair reaches the contrast ratio."""
    values = resolved(scheme=scheme)
    low = [
        (text, ground, round(contrast(values[text], values[ground]), 2))
        for text, ground in PAINTED
        if contrast(values[text], values[ground]) < READABLE
    ]
    assert low == []


def test_chip_text_is_readable_with_the_one_exception() -> None:
    """Chip text is readable with the one exception."""
    assert contrast(CHIP_TEXT, CAMBRIDGE.modes[0].chip) >= READABLE
    light_coach = contrast(CHIP_TEXT, resolved(scheme='light')['--coach'])
    assert COACH_CHIP_LIGHT <= light_coach < READABLE
    assert contrast(CHIP_TEXT, resolved(scheme='dark')['--coach']) >= READABLE


def test_painted_lists_every_pair_the_sheets_declare() -> None:
    """Painted lists every pair the sheets declare."""
    declared = (
        same_rule_pairs(LIVE_CSS)
        | same_rule_pairs(BOARD_CSS)
        | same_rule_pairs(PALETTE_CSS)
    )
    assert declared - {('--chip-text', '--chip-fill')} <= set(PAINTED)


def test_dark_block_redefines_tokens_only_and_all_that_the_kit_does() -> None:
    """Dark block redefines tokens only and all that the kit does."""
    dark = [
        rule
        for rule in css_rules(css=PALETTE_CSS)
        if 'prefers-color-scheme: dark' in rule.context
    ]
    assert [rule.selector for rule in dark] == [':root']
    names = {line.partition(':')[0] for line in dark[0].body}
    assert all(
        name == 'color-scheme' or name.startswith('--') for name in names
    )
    assert 'color-scheme: dark' in dark[0].body
    assert set(tokens(kit_dark_root())) <= names


def test_palette_uses_only_classes_the_shared_pages_have() -> None:
    """Palette uses only classes the shared pages have."""
    assert class_names(PALETTE_CSS) <= (
        class_names(LIVE_CSS) | class_names(BOARD_CSS) | {'mode-coach'}
    )


def test_palette_never_styles_a_bare_now() -> None:
    """Palette never styles a bare now."""
    selectors = [
        part
        for rule in css_rules(css=PALETTE_CSS)
        for part in top_level_split(text=rule.selector)
    ]
    assert '.apps .now' in selectors
    assert [
        selector
        for selector in selectors
        if 'now' in selector_tokens(selector=selector)
        and not selector.startswith('.apps ')
    ] == []


def test_rules_that_board_css_also_sets_double_their_class() -> None:
    """Rules that board css also sets double their class."""
    palette = {
        rule.selector: tokens_and_properties(rule)
        for rule in css_rules(css=PALETTE_CSS)
        if not rule.context
    }
    for rule in css_rules(css=BOARD_CSS):
        if re.fullmatch(r'\.[a-z-]+', rule.selector) and rule.selector in palette:
            assert not palette[rule.selector] & tokens_and_properties(rule), (
                rule.selector
            )


def test_coach_chip_follows_the_scheme() -> None:
    """Coach chip follows the scheme."""
    (rule,) = [
        rule
        for rule in css_rules(css=PALETTE_CSS)
        if rule.selector == '.chip.mode-coach'
    ]
    assert rule.body == ('--chip-fill: var(--coach)',)
    light = resolved(scheme='light')['--coach']
    assert light == '#e03c31' == CAMBRIDGE.modes[1].chip
    assert resolved(scheme='dark')['--coach'] == '#e8554a'
