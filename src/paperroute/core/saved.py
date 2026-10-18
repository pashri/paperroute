"""The Live views the device can show."""

from enum import StrEnum
from typing import Final


class LiveView(StrEnum):
    """The views the bottom knob press switches between."""

    STOP = 'stop'
    MAP = 'map'


LIVE_VIEWS: Final[tuple[LiveView, ...]] = (LiveView.STOP, LiveView.MAP)
