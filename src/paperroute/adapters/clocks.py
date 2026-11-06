"""Clocks: the real one, and one that replays from a chosen moment."""

import time
from datetime import datetime
from pathlib import Path
from typing import Final

from paperroute.core.model import SYDNEY, shifted

TIMESYNC_FLAG: Final[Path] = Path('/run/systemd/timesync/synchronized')


class SystemClock:
    """
    The wall clock in Sydney time.

    Parameters
    ----------
    flag : Path
        File systemd-timesyncd creates once it has set the clock.
    """

    def __init__(self, *, flag: Path = TIMESYNC_FLAG) -> None:
        self.flag = flag

    def synced(self) -> bool:
        """
        Say whether the clock has been synchronised since boot.

        Returns
        -------
        bool
            True once the flag file exists.
        """
        return self.flag.exists()

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

    def synced(self) -> bool:
        """
        Say the replayed time is synchronised.

        Returns
        -------
        bool
            Always True.
        """
        return True
