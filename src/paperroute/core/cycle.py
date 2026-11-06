"""Clock-aligned timing of the poll cycles."""

from datetime import datetime, timedelta

from paperroute.core.alert_state import ALERT_EVERY
from paperroute.core.state_model import Cycle


def boundary(*, now: datetime, every: timedelta) -> datetime:
    """
    Find the latest clock-aligned boundary at or before a time.

    Boundaries are measured in real seconds from local midnight's
    offset, so a repeated hour at a clock change still gives
    boundaries a whole period apart.

    Parameters
    ----------
    now : datetime
        A timezone-aware time.
    every : timedelta
        The period between boundaries.

    Returns
    -------
    datetime
        The boundary, in the same timezone as ``now``.
    """
    ts = now.timestamp()
    off = (now.utcoffset() or timedelta()).total_seconds()
    start = ts - ((ts + off) % every.total_seconds())
    return datetime.fromtimestamp(start, tz=now.tzinfo)


def stale(
    *,
    last: datetime | None,
    now: datetime,
    every: timedelta,
) -> bool:
    """
    Say whether a last time is missing, in the future or before a boundary.

    Times are compared as real instants, because datetimes sharing a zone
    compare by wall clock and the repeated fall-back hour would misorder.

    Parameters
    ----------
    last : datetime | None
        When it last happened.
    now : datetime
        The current time.
    every : timedelta
        The period between boundaries.

    Returns
    -------
    bool
        True when it is due again.
    """
    if last is None or last.timestamp() > now.timestamp():
        return True
    return last.timestamp() < boundary(now=now, every=every).timestamp()


def cycle_due(
    *,
    cycle: Cycle,
    now: datetime,
    every: timedelta,
) -> bool:
    """
    Say whether a new cycle should start.

    Parameters
    ----------
    cycle : Cycle
        The cycle last started.
    now : datetime
        The current time.
    every : timedelta
        The period between cycles.

    Returns
    -------
    bool
        True when none has started, the clock stepped back, or a
        boundary has passed since it started.
    """
    return stale(last=cycle.started, now=now, every=every)


def alerts_due(*, cycle: Cycle, now: datetime) -> bool:
    """
    Say whether the alerts should be asked for again.

    Parameters
    ----------
    cycle : Cycle
        The cycle last started.
    now : datetime
        The current time.

    Returns
    -------
    bool
        True on the same rule as ``cycle_due``, every ``ALERT_EVERY``.
    """
    return stale(last=cycle.alerts_at, now=now, every=ALERT_EVERY)
