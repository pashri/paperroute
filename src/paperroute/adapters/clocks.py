"""Clocks: the real one, and one that replays from a chosen moment."""

import time
from datetime import datetime

from paperroute.core.model import SYDNEY, shifted


class SystemClock:
    """The wall clock in Sydney time."""

    def now(self) -> datetime:
        """
        The current time.

        Returns
        -------
        datetime
            Aware Sydney datetime.
        """
        return datetime.now(SYDNEY)


class ReplayClock:
    """
    Time that starts at a chosen moment and runs at normal speed.

    Parameters
    ----------
    start : datetime
        Aware time to start from.
    """

    def __init__(self, *, start: datetime) -> None:
        self.start = start
        self.began = time.monotonic()

    def now(self) -> datetime:
        """
        The replayed time.

        Returns
        -------
        datetime
            Start plus the whole seconds elapsed since the clock was made.
        """
        elapsed = int(time.monotonic() - self.began)
        return shifted(moment=self.start, seconds=elapsed)
