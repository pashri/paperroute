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
    if estimate is None or abs(estimate.late) < MARK_FLOOR:
        return ''
    sign = f'+{estimate.late}' if estimate.late > 0 else (
        f'{MINUS}{-estimate.late}'
    )
    return f'{sign}?' if estimate.uncertain else sign


def row_mark(*, row: Row) -> str:
    """
    Give the mark for a row.

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
    return late_mark(estimate=row.estimate)
