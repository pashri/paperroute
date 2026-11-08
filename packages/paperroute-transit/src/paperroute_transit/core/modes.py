"""The modes a city declares and how its routes are sorted into them."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
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
        Chip colour, as a CSS hex colour.
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
        `board_with` naming an undeclared id, and per letter that is not
        one character; empty when sound.
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
    return problems
