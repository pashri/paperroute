"""The route chooser's columns and choices."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from paperroute.core.model import RouteKind


class Level(StrEnum):
    """What a chooser column lists."""

    KIND = 'kind'
    GROUP = 'group'
    ROUTE = 'route'
    LINE = 'line'
    TOWARDS = 'towards'
    REPLACEMENTS = 'replacements'


type ChooserPath = tuple[tuple[Level, str], ...]

KIND_ORDER: Final[tuple[RouteKind, ...]] = (
    RouteKind.BUS,
    RouteKind.TRAIN,
    RouteKind.METRO,
    RouteKind.LIGHT_RAIL,
    RouteKind.FERRY,
    RouteKind.SCHOOL,
    RouteKind.REGIONAL,
)
KIND_LABELS: Final[dict[RouteKind, str]] = {
    RouteKind.BUS: 'Bus',
    RouteKind.TRAIN: 'Train',
    RouteKind.METRO: 'Metro',
    RouteKind.LIGHT_RAIL: 'Light rail',
    RouteKind.FERRY: 'Ferry',
    RouteKind.SCHOOL: 'School',
    RouteKind.REGIONAL: 'Regional',
}
KIND_LETTERS: Final[dict[RouteKind, str]] = {
    RouteKind.BUS: 'B',
    RouteKind.TRAIN: 'T',
    RouteKind.METRO: 'M',
    RouteKind.LIGHT_RAIL: 'L',
    RouteKind.FERRY: 'F',
    RouteKind.SCHOOL: 'S',
    RouteKind.REGIONAL: 'R',
}


@dataclass(frozen=True)
class Choice:
    """
    One row of a chooser column.

    Attributes
    ----------
    key : str
        What the row stands for in a chooser path.
    label : str
        Main text.
    badge : str
        Route number, line code or kind letter; '' for none.
    detail : str
        Right-hand text, such as a count; '' for none.
    tag : str
        Short place tag telling repeated route numbers apart; '' for none.
    rule_above : bool
        Whether a rule separates this row from the one above.
    level : Level | None
        The path step's level when it differs from its column's, or None.
    """

    key: str
    label: str
    badge: str = ''
    detail: str = ''
    tag: str = ''
    rule_above: bool = False
    level: Level | None = None


@dataclass(frozen=True)
class Column:
    """
    One chooser column.

    Attributes
    ----------
    level : Level
        What it lists.
    items : tuple[Choice, ...]
        Its rows, in order.
    collapsed : bool
        Whether it was chosen automatically and is not drawn.
    """

    level: Level
    items: tuple[Choice, ...]
    collapsed: bool = False
