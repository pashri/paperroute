"""Space out the starts of requests to a rate-limited API."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Final

MARGIN_S: Final[float] = 0.01  # keeps a closed one-second window to the limit


class Slots:
    """
    Hands out request start slots, first come first served.

    Each slot is ``1 / per_second`` plus a small margin after the one
    before, so no closed one-second window holds more than ``per_second``
    starts.

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
        self.next_at = 0.0

    def delay(self) -> float:
        """
        Take the next slot.

        Returns
        -------
        float
            Seconds to wait until the slot, 0 when it is now.
        """
        now = self.clock()
        slot = max(now, self.next_at)
        self.next_at = slot + self.interval
        return slot - now


class StartLimiter:
    """
    Makes async callers wait for their start slot.

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
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.slots = Slots(per_second=per_second, clock=clock)
        self.sleep = sleep

    async def wait(self) -> None:
        """Return when this caller's start slot arrives."""
        await self.sleep(self.slots.delay())


class SyncStartLimiter:
    """
    Makes blocking callers wait for their start slot.

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
        self.slots = Slots(per_second=per_second, clock=clock)
        self.sleep = sleep

    def wait(self) -> None:
        """Return when this caller's start slot arrives."""
        self.sleep(self.slots.delay())
