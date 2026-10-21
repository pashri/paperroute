"""Tests for the request start limiter, on a fake clock."""

import asyncio

from paperroute.adapters.rate_limit import StartLimiter


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
    """Eight calls at once get slots a quarter second apart."""
    fake = FakeTime()
    limiter = StartLimiter(clock=fake.clock, sleep=fake.sleep)
    for _ in range(8):
        await limiter.wait()
    assert fake.slept_until == [i * 0.25 for i in range(8)]


async def test_idle_limiter_does_not_wait() -> None:
    """A call long after the last slot starts at once."""
    fake = FakeTime()
    seconds: list[float] = []

    async def sleep(delay: float) -> None:
        seconds.append(delay)
        await fake.sleep(delay)

    limiter = StartLimiter(clock=fake.clock, sleep=sleep)
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

    limiter = StartLimiter(clock=fake.clock, sleep=sleep)
    tasks = [asyncio.create_task(limiter.wait()) for _ in range(3)]
    await asyncio.sleep(0)
    tasks[1].cancel()
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[1], asyncio.CancelledError)
    assert results[0] is None
    assert results[2] is None
    assert fake.slept_until == [0.0, 0.5]
