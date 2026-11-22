"""The signed lateness mark shown after a countdown."""

from typing import Final

from paperroute_transit.core.stop_view import Estimate, Pill, Row

MINUS: Final[str] = '−'
MARK_FLOOR: Final[int] = 2  # minutes off before a row is marked


def late_mark(*, estimate: Estimate | None) -> str:
    """
    Write an estimate's lateness as a signed number of minutes.

    Parameters
    ----------
    estimate : Estimate | None
        The row's estimate, if any.

    Returns
    -------
    str
        For example ``+4``, ``−3`` or ``+12?``; empty when there is no
        estimate or it is under the floor.
    """
    if estimate is None:
        return ''
    return signed(late=estimate.late, uncertain=estimate.uncertain)


def signed(*, late: int, uncertain: bool) -> str:
    """
    Write minutes late as a signed number, with ``?`` when uncertain.

    Parameters
    ----------
    late : int
        Minutes late, negative when early.
    uncertain : bool
        True when the lateness is a guess.

    Returns
    -------
    str
        For example ``+4``, ``−3`` or ``+12?``; empty under the floor.
    """
    if abs(late) < MARK_FLOOR:
        return ''
    sign = f'+{late}' if late > 0 else f'{MINUS}{-late}'
    return f'{sign}?' if uncertain else sign


def row_mark(*, row: Row) -> str:
    """
    Give the mark for a row.

    The number is the estimate's when the row has one, else the feed's
    lateness; only an estimate can make it uncertain.

    Parameters
    ----------
    row : Row
        The stop row.

    Returns
    -------
    str
        The mark; empty for timetable and cancelled rows.
    """
    if not row.live or row.pill is Pill.CANCELLED:
        return ''
    if row.estimate is not None:
        return late_mark(estimate=row.estimate)
    if row.late is None:
        return ''
    return signed(late=row.late, uncertain=False)
