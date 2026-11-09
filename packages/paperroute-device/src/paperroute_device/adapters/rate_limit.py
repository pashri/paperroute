"""Space out the starts of requests to a rate-limited API."""

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Final

MARGIN_S: Final[float] = 0.01  # keeps a closed one-second window to the limit


class Starts:
    """
    Remembers when the last request really started.

    Starts are kept ``1 / per_second`` plus a small margin apart, so no
    closed one-second window holds more than ``per_second`` starts.

    Parameters
    ----------
    per_second : float
        Most request starts allowed in one second.
    clock : Callable[[], float]
        Returns the current time in seconds.
    """

    def __init__(
        self, *, per_second: float, clock: Callable[[], float],
    ) -> None:
        self.interval = 1 / per_second + MARGIN_S
        self.clock = clock
        self.last = -math.inf

    def delay(self) -> float:
        """
        Say how long until the next start is allowed.

        Returns
        -------
        float
            Seconds to wait, 0 when it is allowed now.
        """
        return max(self.last + self.interval - self.clock(), 0.0)

    def started(self) -> None:
        """Record a start at the current time."""
        self.last = self.clock()


class StartLimiter:
    """
    Makes async callers wait their turn to start, one at a time.

    Callers are never dropped. The clock is read again after each wait,
    so a stalled event loop delays starts but never bunches them.

    Parameters
    ----------
    per_second : float
        Most request starts allowed in one second.
    clock : Callable[[], float]
        Returns the current time in seconds.
    sleep : Callable[[float], Awaitable[None]]
        Waits for the given number of seconds.
    """

    def __init__(
        self,
        *,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.starts = Starts(per_second=per_second, clock=clock)
        self.sleep = sleep
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        """Return when this caller may start, and count it as started."""
        async with self.lock:
            await self.sleep(self.starts.delay())
            self.starts.started()


class SyncStartLimiter:
    """
    Makes blocking callers wait their turn to start, one at a time.

    Parameters
    ----------
    per_second : float
        Most request starts allowed in one second.
    clock : Callable[[], float]
        Returns the current time in seconds.
    sleep : Callable[[float], None]
        Blocks for the given number of seconds.
    """

    def __init__(
        self,
        *,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.starts = Starts(per_second=per_second, clock=clock)
        self.sleep = sleep
        self.lock = threading.Lock()

    def wait(self) -> None:
        """Return when this caller may start, and count it as started."""
        with self.lock:
            self.sleep(self.starts.delay())
            self.starts.started()
