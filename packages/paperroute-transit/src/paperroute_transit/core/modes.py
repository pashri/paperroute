"""The modes a city declares and how its routes are sorted into them."""

import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from paperroute_transit.core.chooser import Choice

MODE_ID: Final[re.Pattern[str]] = re.compile(r'^[a-z][a-z0-9_]*$')
HEX_COLOUR: Final[re.Pattern[str]] = re.compile(r'^#[0-9a-f]{6}$')


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class ModeSpec:
    """
    One mode of a city's routes.

    Attributes
    ----------
    id : str
        Mode id, as pins and the timetable store it.
    label : str
        Name shown in the chooser and the pin editor.
    letter : str
        One-character chip letter.
    chip : str
        Chip colour, as a lower-case six-digit CSS hex colour.
    by_line : bool
        True when its routes are chosen as lines, station first.
    under : str | None
        The mode whose chooser row it sits under, or None.
    replaces : str | None
        The mode whose lines its routes stand in for, or None.
    board_with : tuple[str, ...]
        Further modes a board of this mode shows.
    named_alerts : bool
        True when alerts name its lines.
    loose_alerts : bool
        True when its alerts match loosely.
    wharf_names : bool
        True when its alerts name stops by wharf.
    tagged : bool
        True when repeated route numbers get place tags.
    skippable : bool
        True when a setting may hide it.
    chip_text : str
        Chip text colour, as a lower-case six-digit CSS hex colour.
    named_lines : bool
        True when one number heads several lines, told apart by their long
        names.
    destination_first : bool
        True when the phone editor asks for the Destination before the
        line.
    platform_notes : bool
        True when later rows note their platform.
    stop_positions : bool
        True when the Stop view fetches its hero's positions for a pin
        with no Destination.
    """

    id: str
    label: str
    letter: str
    chip: str
    by_line: bool = False
    under: str | None = None
    replaces: str | None = None
    board_with: tuple[str, ...] = ()
    named_alerts: bool = False
    loose_alerts: bool = False
    wharf_names: bool = False
    tagged: bool = False
    skippable: bool = False
    chip_text: str = '#ffffff'
    named_lines: bool = False
    destination_first: bool = False
    platform_notes: bool = False
    stop_positions: bool = False


@dataclass(frozen=True)
class RouteClass:
    """
    The mode and feed source a route belongs to.

    Attributes
    ----------
    kind : str | None
        Mode id, or None for a route no mode takes.
    source : str
        The city's feed key for the route; opaque to the engine.
    """

    kind: str | None
    source: str


class Classifier(Protocol):
    """Sorts a route into a mode and a feed source."""

    def __call__(
        self, *, route_type: int, agency_id: str | None, short_name: str,
    ) -> RouteClass:
        """
        Classify one route.

        Parameters
        ----------
        route_type : int
            GTFS route_type.
        agency_id : str | None
            GTFS agency_id, when the feed gives one.
        short_name : str
            Route short name.

        Returns
        -------
        RouteClass
            Its mode and feed source.
        """


def spec_of(
    *, modes: Sequence[ModeSpec], mode: str | None,
) -> ModeSpec | None:
    """
    Find a mode's spec.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.
    mode : str | None
        Mode id, or None.

    Returns
    -------
    ModeSpec | None
        The spec with that id; None for None or an undeclared id.
    """
    return next((spec for spec in modes if spec.id == mode), None)


def line_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes chosen by line.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `by_line` set.
    """
    return frozenset(spec.id for spec in modes if spec.by_line)


def named_line_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes whose lines are keyed by long name.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `named_lines` set.
    """
    return frozenset(spec.id for spec in modes if spec.named_lines)


def destination_first_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes whose editor asks for the Destination first.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `destination_first` set.
    """
    return frozenset(spec.id for spec in modes if spec.destination_first)


def line_key(
    *, kind: str, number: str, long_name: str, named: Collection[str],
) -> str:
    """
    Give a line's chooser key.

    Parameters
    ----------
    kind : str
        Mode id.
    number : str
        The line's number.
    long_name : str
        The line's long name.
    named : Collection[str]
        Ids of the modes keyed by long name.

    Returns
    -------
    str
        `kind:number:long_name` for a named mode, else `kind:number`.
    """
    if kind in named:
        return f'{kind}:{number}:{long_name}'
    return f'{kind}:{number}'


def line_value(*, key: str, kind: str) -> str:
    """
    Drop the mode from a line key.

    Parameters
    ----------
    key : str
        A line key.
    kind : str
        Its mode id.

    Returns
    -------
    str
        The key without its `kind:` prefix.
    """
    return key.removeprefix(f'{kind}:')


def line_badge(*, value: str) -> str:
    """
    Give the number of a line value.

    Parameters
    ----------
    value : str
        A line value, `number` or `number:long_name`.

    Returns
    -------
    str
        The text before the first colon, or all of it.
    """
    return value.split(':', 1)[0]


def pin_value(*, choice: 'Choice') -> str:
    """
    What a pin stores for a chosen line or route.

    Parameters
    ----------
    choice : Choice
        The chosen line or route.

    Returns
    -------
    str
        The choice's value, else its badge.
    """
    return choice.value or choice.badge


def tagged_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes whose repeated route numbers get place tags.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `tagged` set.
    """
    return frozenset(spec.id for spec in modes if spec.tagged)



def skippable_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes a setting may hide.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `skippable` set.
    """
    return frozenset(spec.id for spec in modes if spec.skippable)


def replacing_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes whose routes stand in for another mode's lines.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `replaces` set.
    """
    return frozenset(spec.id for spec in modes if spec.replaces is not None)


def replaced_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes whose lines another mode's routes stand in for.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        The ids named by a spec's `replaces`.
    """
    return frozenset(
        spec.replaces for spec in modes if spec.replaces is not None
    )


def wharf_modes(*, modes: Sequence[ModeSpec]) -> frozenset[str]:
    """
    List the modes whose stops are wharves.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    frozenset[str]
        Ids of the specs with `wharf_names` set.
    """
    return frozenset(spec.id for spec in modes if spec.wharf_names)

def mode_problems(*, modes: Sequence[ModeSpec]) -> list[str]:
    """
    Find what is wrong with a city's declared modes.

    Parameters
    ----------
    modes : Sequence[ModeSpec]
        Declared modes.

    Returns
    -------
    list[str]
        One message per duplicate id, per `under`, `replaces` or
        `board_with` naming an undeclared id, per letter that is not one
        character, per id that is not lower-case letters, digits and
        underscores, and per chip colour that is not a lower-case
        six-digit hex colour; empty when sound.
    """
    ids = Counter(spec.id for spec in modes)
    problems = [
        f'mode {mode!r} is declared {count} times'
        for mode, count in ids.items()
        if count > 1
    ]
    for spec in modes:
        named = [
            *(() if spec.under is None else (('under', spec.under),)),
            *(() if spec.replaces is None else (('replaces', spec.replaces),)),
            *(('board_with', other) for other in spec.board_with),
        ]
        problems.extend(
            f'mode {spec.id!r} {field} names undeclared {other!r}'
            for field, other in named
            if other not in ids
        )
        if len(spec.letter) != 1:
            problems.append(
                f'mode {spec.id!r} letter {spec.letter!r} is not one '
                'character',
            )
        problems.extend(css_problems(spec=spec))
    return problems


def css_problems(*, spec: ModeSpec) -> list[str]:
    """
    Find the values of one mode that are unsafe to write into CSS.

    Parameters
    ----------
    spec : ModeSpec
        The mode.

    Returns
    -------
    list[str]
        One message for an id that is not lower-case letters, digits and
        underscores, and one per chip colour that is not a lower-case
        six-digit hex colour; empty when safe.
    """
    problems = (
        []
        if MODE_ID.fullmatch(spec.id)
        else [f'mode {spec.id!r} id is not lower-case letters and digits']
    )
    problems.extend(
        f'mode {spec.id!r} {field} {value!r} is not a #rrggbb colour'
        for field, value in (
            ('chip', spec.chip),
            ('chip_text', spec.chip_text),
        )
        if not HEX_COLOUR.fullmatch(value)
    )
    return problems
