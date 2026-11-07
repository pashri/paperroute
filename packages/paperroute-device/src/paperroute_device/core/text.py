"""Separators, clock times and days the device's screens print."""

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


def local_time(*, stamp: str | None, zone: tzinfo) -> datetime | None:
    """
    Read an ISO time as a wall-clock time in a zone.

    Parameters
    ----------
    stamp : str | None
        An ISO 8601 time, or None.
    zone : tzinfo
        Time zone whose wall clock to give.

    Returns
    -------
    datetime | None
        The time in ``zone``; None when missing or unreadable.
    """
    if stamp is None:
        return None
    try:
        return datetime.fromisoformat(stamp).astimezone(zone)
    except ValueError:
        return None


def day_text(*, moment: datetime) -> str:
    """
    Give a day as '21 Oct'.

    Parameters
    ----------
    moment : datetime
        The time.

    Returns
    -------
    str
        Day of the month and short month name.
    """
    return f'{moment.day} {moment:%b}'
