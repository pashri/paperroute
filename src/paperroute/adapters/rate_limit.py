"""Space out the starts of requests to a rate-limited API."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Final

TFNSW_STARTS_PER_S: Final[float] = 4.0


class StartLimiter:
    """
    Hands out request start slots, first come first served.

    Each slot is at least ``1 / per_second`` after the one before.
    Callers are never dropped; they wait for their slot.

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
        per_second: float = TFNSW_STARTS_PER_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.interval = 1 / per_second
        self.clock = clock
        self.sleep = sleep
        self.next_at = 0.0

    async def wait(self) -> None:
        """Return when this caller's start slot arrives."""
        now = self.clock()
        slot = max(now, self.next_at)
        self.next_at = slot + self.interval
        await self.sleep(slot - now)
