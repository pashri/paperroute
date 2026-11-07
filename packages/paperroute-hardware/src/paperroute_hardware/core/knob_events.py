"""What the knobs do: turns, presses and holds."""

from dataclasses import dataclass
from enum import StrEnum


class Knob(StrEnum):
    """The two knobs."""

    TOP = 'top'
    BOTTOM = 'bottom'


@dataclass(frozen=True)
class KnobTurned:
    """A knob turned by some detents (negative is anticlockwise)."""

    knob: Knob
    steps: int


@dataclass(frozen=True)
class KnobPressed:
    """A knob was pressed."""

    knob: Knob


@dataclass(frozen=True)
class KnobHeld:
    """A knob was held down and let go, with no turn while held."""

    knob: Knob


@dataclass(frozen=True)
class KnobHeldTurned:
    """A knob turned by some detents while held down."""

    knob: Knob
    steps: int


@dataclass(frozen=True)
class KnobsHeld:
    """Both knobs were held down together for the hold time."""


KnobEvent = KnobTurned | KnobPressed | KnobHeld | KnobHeldTurned
KnobInput = KnobEvent | KnobsHeld
