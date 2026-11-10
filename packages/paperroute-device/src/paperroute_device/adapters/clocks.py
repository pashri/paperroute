"""Clocks: the real one, and one that replays from a chosen moment."""

import time
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Final

TIMESYNC_FLAG: Final[Path] = Path('/run/systemd/timesync/synchronized')


def shifted(*, moment: datetime, seconds: int, zone: tzinfo) -> datetime:
    """
    Move a moment by real seconds and give the result in local time.

    The sum is done in UTC because Python adds to an aware datetime on the
    wall clock, which is wrong across a daylight-saving change.

    Parameters
    ----------
    moment : datetime
        Aware time.
    seconds : int
        Real seconds to add (negative to subtract).
    zone : tzinfo
        Local time zone.

    Returns
    -------
    datetime
        Aware local datetime.
    """
    return (moment.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(
        zone,
    )


class SystemClock:
    """
    The wall clock in local time.

    Parameters
    ----------
    zone : tzinfo
        Local time zone.
    flag : Path
        File systemd-timesyncd creates once it has set the clock.
    """

    def __init__(
        self, *, zone: tzinfo, flag: Path = TIMESYNC_FLAG,
    ) -> None:
        self.zone = zone
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
            Aware local datetime.
        """
        return datetime.now(self.zone)


class ReplayClock:
    """
    Time that starts at a chosen moment and runs at normal speed.

    Parameters
    ----------
    start : datetime
        Aware time to start from.
    zone : tzinfo
        Local time zone the replayed times are given in.
    """

    def __init__(self, *, start: datetime, zone: tzinfo) -> None:
        self.start = start
        self.zone = zone
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
        return shifted(moment=self.start, seconds=elapsed, zone=self.zone)

    def synced(self) -> bool:
        """
        Say the replayed time is synchronised.

        Returns
        -------
        bool
            Always True.
        """
        return True
