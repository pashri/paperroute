"""Tests for the request start limiter, on a fake clock."""

import asyncio
from typing import Final

import pytest

from paperroute_device.adapters.rate_limit import (
    StartLimiter,
    SyncStartLimiter,
)

PER_SECOND: Final[float] = 3.0  # starts
STEP: Final[float] = 1 / PER_SECOND + 0.01  # seconds between starts


class SteppedTime:
    """A clock that moves only when told to, waking sleepers as it goes."""

    def __init__(self) -> None:
        self.now = 0.0
        self.moved = asyncio.Condition()

    def clock(self) -> float:
        """
        Read the fake time.

        Returns
        -------
        float
            Seconds.
        """
        return self.now

    async def sleep(self, seconds: float) -> None:
        """
        Wait until the fake time reaches now plus the given seconds.

        Parameters
        ----------
        seconds : float
            How long to wait.
        """
        until = self.now + seconds
        async with self.moved:
            await self.moved.wait_for(lambda: self.now >= until)

    async def move_to(self, *, now: float) -> None:
        """
        Set the fake time, wake the sleepers it reaches and let them run.

        Parameters
        ----------
        now : float
            The new time in seconds.
        """
        async with self.moved:
            self.now = now
            self.moved.notify_all()
        for _ in range(20):
            await asyncio.sleep(0)


class FakeTime:
    """A clock whose sleep moves it forward and records the target."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept_until: list[float] = []

    def clock(self) -> float:
        """
        Read the fake time.

        Returns
        -------
        float
            Seconds.
        """
        return self.now

    async def sleep(self, seconds: float) -> None:
        """
        Advance the fake time and note where it ended up.

        Parameters
        ----------
        seconds : float
            How long to wait.
        """
        self.now += seconds
        self.slept_until.append(self.now)


async def test_starts_are_spaced() -> None:
    """Eight calls at once get slots just over a third of a second apart."""
    fake = FakeTime()
    limiter = StartLimiter(
        per_second=PER_SECOND, clock=fake.clock, sleep=fake.sleep,
    )
    for _ in range(8):
        await limiter.wait()
    assert fake.slept_until == pytest.approx([i * STEP for i in range(8)])


async def test_no_closed_second_holds_four_starts() -> None:
    """Any window of one second, ends included, holds at most three starts."""
    fake = FakeTime()
    limiter = StartLimiter(
        per_second=PER_SECOND, clock=fake.clock, sleep=fake.sleep,
    )
    for _ in range(40):
        await limiter.wait()
    starts = fake.slept_until
    for first in starts:
        assert sum(first <= s <= first + 1 for s in starts) <= 3


async def test_idle_limiter_does_not_wait() -> None:
    """A call long after the last slot starts at once."""
    fake = FakeTime()
    seconds: list[float] = []

    async def sleep(delay: float) -> None:
        seconds.append(delay)
        await fake.sleep(delay)

    limiter = StartLimiter(
        per_second=PER_SECOND, clock=fake.clock, sleep=sleep,
    )
    await limiter.wait()
    fake.now += 2
    await limiter.wait()
    assert seconds[-1] == 0


async def test_cancelled_waiter_leaves_later_slots_alone() -> None:
    """Cancelling one queued waiter holds nobody up and skips no slot."""
    fake = FakeTime()
    release = asyncio.Event()

    async def sleep(delay: float) -> None:
        await release.wait()
        await fake.sleep(delay)

    limiter = StartLimiter(
        per_second=PER_SECOND, clock=fake.clock, sleep=sleep,
    )
    tasks = [asyncio.create_task(limiter.wait()) for _ in range(3)]
    await asyncio.sleep(0)
    tasks[1].cancel()
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[1], asyncio.CancelledError)
    assert results[0] is None
    assert results[2] is None
    assert fake.slept_until == pytest.approx([0.0, STEP])


async def test_stall_never_bunches_starts() -> None:
    """A clock jump past several slots still leaves real starts spaced."""
    fake = SteppedTime()
    limiter = StartLimiter(
        per_second=PER_SECOND, clock=fake.clock, sleep=fake.sleep,
    )
    starts: list[float] = []

    async def caller() -> None:
        await limiter.wait()
        starts.append(fake.clock())

    tasks = [asyncio.create_task(caller()) for _ in range(6)]
    await fake.move_to(now=0.0)
    await fake.move_to(now=2.0)
    while len(starts) < len(tasks):
        await fake.move_to(now=fake.now + 0.05)
    await asyncio.gather(*tasks)
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    assert min(gaps) >= STEP


async def test_uncontended_call_waits_only_the_rest() -> None:
    """A call part way through the interval waits only for what is left."""
    fake = FakeTime()
    seconds: list[float] = []

    async def sleep(delay: float) -> None:
        seconds.append(delay)
        await fake.sleep(delay)

    limiter = StartLimiter(
        per_second=PER_SECOND, clock=fake.clock, sleep=sleep,
    )
    await limiter.wait()
    fake.now += STEP / 4
    await limiter.wait()
    assert seconds == pytest.approx([0.0, 3 * STEP / 4])


def test_sync_limiter_spaces_blocking_starts() -> None:
    """Blocking callers get the same slots as async ones."""
    fake = FakeTime()
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        fake.now += seconds
        slept.append(fake.now)

    limiter = SyncStartLimiter(per_second=1, clock=fake.clock, sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == pytest.approx([0.0, 1.01, 2.02])
