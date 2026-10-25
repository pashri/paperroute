"""Auto windows and the occurrences they run, as stored in state.json."""

import logging
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from paperroute.core.auto_model import Occurrence
from paperroute.core.model import DAY_NAMES, SYDNEY, AutoWindow

logger = logging.getLogger(__name__)

CLOCK: Final[str] = r'^([01]\d|2[0-3]):[0-5]\d$'
CLOCK_FORMAT: Final[str] = '%H:%M'
EMPTY_AUTO: Final[dict[str, None]] = {
    'occurrence': None, 'before': None, 'overridden_at': None,
}

type DayName = Literal['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


class AutoWindowModel(BaseModel):
    """A pin's window as stored, each end as ``HH:MM``."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    days: tuple[DayName, ...] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=CLOCK)
    end: str = Field(pattern=CLOCK)

    @field_validator('days')
    @classmethod
    def check_days(cls, days: tuple[DayName, ...]) -> tuple[DayName, ...]:
        """
        Refuse a day named twice.

        Parameters
        ----------
        days : tuple[DayName, ...]
            The stored days.

        Returns
        -------
        tuple[DayName, ...]
            The days, unchanged.

        Raises
        ------
        ValueError
            When a day appears more than once.
        """
        if len(set(days)) != len(days):
            raise ValueError('a window names each day once')
        return days

    @model_validator(mode='after')
    def check_length(self) -> Self:
        """
        Refuse a window that starts and ends at the same time.

        Returns
        -------
        Self
            The window, unchanged.

        Raises
        ------
        ValueError
            When the start and end match.
        """
        if self.start == self.end:
            raise ValueError("a window can't start and end together")
        return self


class OccurrenceModel(BaseModel):
    """One run of a window as stored."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    ref: str
    start: AwareDatetime
    end: AwareDatetime


def window_to_model(*, window: AutoWindow) -> AutoWindowModel:
    """
    Convert a pin's window to its stored form.

    Parameters
    ----------
    window : AutoWindow
        The window.

    Returns
    -------
    AutoWindowModel
        The window with its days as names, Monday first.
    """
    return AutoWindowModel.model_validate({
        'days': [DAY_NAMES[day] for day in sorted(window.days)],
        'start': window.start.strftime(CLOCK_FORMAT),
        'end': window.end.strftime(CLOCK_FORMAT),
    })


def window_from_model(*, model: AutoWindowModel) -> AutoWindow:
    """
    Convert a stored window back.

    Parameters
    ----------
    model : AutoWindowModel
        The stored window.

    Returns
    -------
    AutoWindow
        The window.
    """
    return AutoWindow(
        days=frozenset(DAY_NAMES.index(day) for day in model.days),
        start=datetime.strptime(model.start, CLOCK_FORMAT).time(),
        end=datetime.strptime(model.end, CLOCK_FORMAT).time(),
    )


def occurrence_to_model(*, occurrence: Occurrence) -> OccurrenceModel:
    """
    Convert an occurrence to its stored form.

    Parameters
    ----------
    occurrence : Occurrence
        The occurrence.

    Returns
    -------
    OccurrenceModel
        The stored occurrence.
    """
    return OccurrenceModel(
        ref=occurrence.ref, start=occurrence.start, end=occurrence.end,
    )


def occurrence_from_model(*, model: OccurrenceModel) -> Occurrence:
    """
    Convert a stored occurrence back.

    Parameters
    ----------
    model : OccurrenceModel
        The stored occurrence.

    Returns
    -------
    Occurrence
        The occurrence, its times in Sydney.
    """
    return Occurrence(
        ref=model.ref,
        start=model.start.astimezone(SYDNEY),
        end=model.end.astimezone(SYDNEY),
    )


def with_windows(*, pin: object) -> object:
    """
    Give one stored pin an empty list of windows.

    Parameters
    ----------
    pin : object
        A stored pin; anything that is not an object is returned as it
        is, for validation to fail.

    Returns
    -------
    object
        The pin with `windows` empty unless it already has some.
    """
    return {'windows': [], **pin} if isinstance(pin, dict) else pin


def add_auto(data: dict[str, object]) -> dict[str, object]:
    """
    Give every pin windows and add an empty auto block.

    Parameters
    ----------
    data : dict[str, object]
        Version 7 data.

    Returns
    -------
    dict[str, object]
        The data with `windows` empty on each pin and on the unpinned
        place, and `auto` with every field None.
    """
    pins = data.get('pins')
    return {
        **data,
        'pins': (
            [with_windows(pin=pin) for pin in pins]
            if isinstance(pins, list)
            else pins
        ),
        'unpinned': with_windows(pin=data.get('unpinned')),
        'auto': dict(EMPTY_AUTO),
    }


def readable(*, window: object) -> bool:
    """
    Say whether a stored window can be read, warning when not.

    Parameters
    ----------
    window : object
        The stored window.

    Returns
    -------
    bool
        True when it validates.
    """
    try:
        AutoWindowModel.model_validate(window)
    except ValidationError:
        logger.warning('state.json pin window unusable, dropping it')
        return False
    return True


def repaired_windows(*, pin: dict[str, object]) -> dict[str, object]:
    """
    Drop each of a stored pin's windows that cannot be read.

    Parameters
    ----------
    pin : dict[str, object]
        The stored pin.

    Returns
    -------
    dict[str, object]
        The pin with only its readable windows; none when `windows` is
        missing or not a list.
    """
    windows = pin.get('windows')
    if not isinstance(windows, list):
        return {**pin, 'windows': []}
    return {
        **pin,
        'windows': [
            window for window in windows if readable(window=window)
        ],
    }
