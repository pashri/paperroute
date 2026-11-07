"""Separators and clock times the device's screens print."""

from datetime import datetime, tzinfo
from typing import Final

DOT: Final[str] = ' · '
HINT_DOT: Final[str] = ' • '  # between the parts of a knob hint


def clock_text(*, moment: datetime, zone: tzinfo) -> str:
    """
    A 24-hour clock time without a leading zero.

    Parameters
    ----------
    moment : datetime
        Time.
    zone : tzinfo
        Time zone whose wall clock to show.

    Returns
    -------
    str
        For example '8:05'.
    """
    local = moment.astimezone(zone)
    return f'{local.hour}:{local.minute:02d}'
