"""Tests for writing a city's mode chip colours as CSS."""

from importlib.resources import files

import pytest

from paperroute.adapters.mode_css import mode_css
from paperroute.city import SYDNEY
from paperroute_transit.core.modes import ModeSpec
from tests.css_rules import css_rules

OLD_CHIPS: dict[str, tuple[str, str]] = {
    'bus': ('#00b5ef', '#000000'),
    'school': ('#00b5ef', '#000000'),
    'replacement': ('#00b5ef', '#000000'),
    'train': ('#f99d1c', '#000000'),
    'metro': ('#168388', '#ffffff'),
    'light_rail': ('#ee343f', '#000000'),
    'ferry': ('#5ab031', '#000000'),
    'coach': ('#732a82', '#ffffff'),
}


def declared(*, css: str, selector: str) -> dict[str, str]:
    """
    Read the declarations of one rule.

    Parameters
    ----------
    css : str
        A stylesheet.
    selector : str
        The rule's selector.

    Returns
    -------
    dict[str, str]
        Each property's value, by name.
    """
    return {
        name.strip(): value.strip()
        for rule in css_rules(css=css)
        if rule.selector == selector
        for name, value in (line.split(':', 1) for line in rule.body)
    }


def test_sydney_chips_keep_old_colours() -> None:
    """Each Sydney mode's chip fill and text stay as they were."""
    css = mode_css(modes=SYDNEY.modes)
    tokens = declared(css=css, selector=':root')
    chips = {}
    for mode in OLD_CHIPS:
        name = mode.replace('_', '-')
        rule = declared(css=css, selector=f'.mode-{name}')
        assert rule['--chip-fill'] == f'var(--mode-{name})'
        chips[mode] = (
            tokens[f'--mode-{name}'],
            rule.get('--chip-text', '#ffffff'),
        )
    assert chips == OLD_CHIPS


def test_bad_colour_refused() -> None:
    """A chip colour that could break out of its rule is refused."""
    bad = ModeSpec(id='bus', label='Bus', letter='B', chip='red;}')
    with pytest.raises(ValueError, match='chip'):
        mode_css(modes=[bad])


def test_chip_text_default_matches_css() -> None:
    """The board's plain chip text is the mode spec's default."""
    css = (files('paperroute') / 'assets' / 'web' / 'board.css').read_text(
        encoding='utf-8',
    )
    default = ModeSpec(id='x', label='X', letter='X', chip='#000000')
    assert declared(css=css, selector='.chip')['--chip-text'] == (
        default.chip_text
    )
