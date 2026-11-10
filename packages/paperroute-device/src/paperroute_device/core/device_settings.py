"""The settings the device keeps for itself, whatever board it shows."""

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Final

from paperroute_hardware.ports import CLEAN_AFTER


@dataclass(frozen=True, order=True)
class Window:
    """
    A daily stretch of local time, from start up to end.

    Attributes
    ----------
    start : time
        The first moment inside.
    end : time
        The first moment after.
    """

    start: time
    end: time


class QuietMode(StrEnum):
    """How quiet hours are chosen."""

    MY_HOURS = 'my_hours'
    APP = 'app'


MY_HOURS: Final[Window] = Window(start=time(23), end=time(5, 30))
CLEAN_CHOICES: Final[tuple[int, ...]] = (15, 30, 60, 120)  # quick patches


@dataclass(frozen=True)
class DeviceSettings:  # pylint: disable=too-many-instance-attributes
    """
    The device's own choices on the Settings page.

    Attributes
    ----------
    quiet_mode : QuietMode
        How quiet hours are chosen.
    my_hours : Window
        The quiet hours chosen by hand; may wrap midnight.
    rotate : bool
        Whether the panel is turned 180 degrees.
    larger_text : bool
        Whether list rows use larger text.
    inverted_lists : bool
        Whether list rows are white on black.
    clean_after : int
        Quick patches the panel draws before a cleaning full refresh;
        one of `CLEAN_CHOICES`.
    swap_knobs : bool
        Whether knob A plays the bottom knob and knob B the top.
    """

    quiet_mode: QuietMode = QuietMode.MY_HOURS
    my_hours: Window = MY_HOURS
    rotate: bool = False
    larger_text: bool = False
    inverted_lists: bool = False
    clean_after: int = CLEAN_AFTER
    swap_knobs: bool = False
