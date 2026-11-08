"""The mode chip colours, written as CSS from a city's modes."""

from collections.abc import Sequence
from typing import Final

from paperroute_transit.core.modes import ModeSpec, mode_problems

MODES_MARK: Final[str] = '/* {{modes}} */'
WHITE_TEXT: Final[str] = '#ffffff'


def css_id(*, spec: ModeSpec) -> str:
    """
    Name a mode as CSS names it.

    Parameters
    ----------
    spec : ModeSpec
        The mode.

    Returns
    -------
    str
        The mode id with each underscore as a hyphen.
    """
    return spec.id.replace('_', '-')


def chip_rule(*, spec: ModeSpec) -> str:
    """
    Write one mode's chip rule.

    Parameters
    ----------
    spec : ModeSpec
        The mode.

    Returns
    -------
    str
        The `.mode-<id>` rule, with no trailing newline. It sets the text
        colour only when it is not white, the chip's own default.
    """
    name = css_id(spec=spec)
    text = (
        ()
        if spec.chip_text == WHITE_TEXT
        else (f'  --chip-text: {spec.chip_text};',)
    )
    return '\n'.join((
        f'.mode-{name} {{',
        f'  --chip-fill: var(--mode-{name});',
        *text,
        '}',
    ))


def mode_css(*, modes: Sequence[ModeSpec]) -> str:
    """
    Write the mode colour tokens and one chip rule per mode.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes, in order.

    Returns
    -------
    str
        A `:root` rule with a `--mode-<id>` token per mode, then each
        mode's chip rule, separated by blank lines, with no trailing
        newline.

    Raises
    ------
    ValueError
        When the modes have any problem, since their values are written
        into CSS.
    """
    problems = mode_problems(modes=modes)
    if problems:
        raise ValueError('; '.join(problems))
    tokens = '\n'.join((
        ':root {',
        *(f'  --mode-{css_id(spec=spec)}: {spec.chip};' for spec in modes),
        '}',
    ))
    return '\n\n'.join((tokens, *(chip_rule(spec=spec) for spec in modes)))


def board_css(*, text: str, modes: Sequence[ModeSpec]) -> str:
    """
    Fill a board stylesheet's modes mark with the city's chip rules.

    Parameters
    ----------
    text : str
        The stylesheet, holding the modes mark on a line of its own.
    modes : Sequence[ModeSpec]
        Declared modes, in order.

    Returns
    -------
    str
        The stylesheet with the mark replaced.

    Raises
    ------
    ValueError
        When the modes have any problem.
    """
    return text.replace(MODES_MARK, mode_css(modes=modes), 1)
