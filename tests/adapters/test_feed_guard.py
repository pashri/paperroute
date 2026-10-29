"""Tests for backing off throttled feeds and sharing fetches in flight."""

import asyncio
from typing import Final

import pytest

from paperroute.adapters.feed_guard import (
    FeedBackingOff,
    GuardedFeeds,
    backoff_s,
)
from paperroute.adapters.tfnsw import FeedError, FeedThrottled, FeedUnavailable

BUSES: Final[str] = 'v1/gtfs/vehiclepos/buses'
TRAINS: Final[str] = 'v2/gtfs/vehiclepos/sydneytrains'


class ScriptedFeeds:
    """A feeds client that answers each feed from a script, in order."""

    def __init__(self, *, script: dict[str, list[bytes | FeedError]]) -> None:
        self.script = script
        self.calls: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.closed = False

    async def fetch(self, *, feed: str) -> bytes:
        """
        Give the feed's next scripted answer once the gate is open.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            The scripted body.

        Raises
        ------
        FeedError
            When the script says so.
        """
        self.calls.append(feed)
        await self.gate.wait()
        answer = self.script[feed].pop(0)
        if isinstance(answer, FeedError):
            raise answer
        return answer

    async def close(self) -> None:
        """Note the close."""
        self.closed = True


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        """
        Read the time.

        Returns
        -------
        float
            Seconds.
        """
        return self.now


def throttled(*, feed: str = BUSES, after: float | None = None) -> FeedError:
    """
    Make a 429.

    Parameters
    ----------
    feed : str
        Feed path.
    after : float | None
        Retry-After seconds.

    Returns
    -------
    FeedError
        The error.
    """
    return FeedThrottled(feed=feed, retry_after_s=after)


def guarded(
    *, script: dict[str, list[bytes | FeedError]],
) -> tuple[GuardedFeeds, ScriptedFeeds, Clock]:
    """
    Wrap a scripted client.

    Parameters
    ----------
    script : dict[str, list[bytes | FeedError]]
        Answers by feed.

    Returns
    -------
    tuple[GuardedFeeds, ScriptedFeeds, Clock]
        The guard, the client inside it and the guard's clock.
    """
    inner = ScriptedFeeds(script=script)
    clock = Clock()
    return GuardedFeeds(inner=inner, clock=clock), inner, clock


@pytest.mark.parametrize(
    ('strikes', 'seconds'),
    [(1, 30), (2, 60), (3, 120), (4, 240), (5, 300), (9, 300)],
)
def test_backoff_s_doubles_to_five_minutes(
    strikes: int, seconds: float,
) -> None:
    """Each 429 in a row doubles the wait, from 30 s up to 5 minutes."""
    assert backoff_s(strikes=strikes, retry_after_s=None) == seconds


def test_backoff_s_honours_longer_retry_after() -> None:
    """A Retry-After longer than the doubling wins over it."""
    assert backoff_s(strikes=2, retry_after_s=90) == 90


def test_backoff_s_short_retry_after_keeps_doubling() -> None:
    """A Retry-After shorter than the doubling does not undo it."""
    assert backoff_s(strikes=4, retry_after_s=12) == 240


@pytest.mark.parametrize('retry_after_s', [301, 86400, 1e12])
def test_backoff_s_caps_retry_after(retry_after_s: float) -> None:
    """A huge Retry-After is cut to five minutes."""
    assert backoff_s(strikes=1, retry_after_s=retry_after_s) == 300


async def test_fetch_passes_through() -> None:
    """A good answer comes back as it is."""
    guard, inner, _ = guarded(script={BUSES: [b'one']})
    assert await guard.fetch(feed=BUSES) == b'one'
    assert inner.calls == [BUSES]


async def test_429_backs_off_that_feed_only() -> None:
    """After a 429 the feed sends nothing for 30 s; others carry on."""
    guard, inner, clock = guarded(
        script={BUSES: [throttled(), b'later'], TRAINS: [b'trains']},
    )
    with pytest.raises(FeedThrottled):
        await guard.fetch(feed=BUSES)
    clock.now += 29
    with pytest.raises(FeedBackingOff, match='backing off'):
        await guard.fetch(feed=BUSES)
    assert await guard.fetch(feed=TRAINS) == b'trains'
    clock.now += 1
    assert await guard.fetch(feed=BUSES) == b'later'
    assert inner.calls == [BUSES, TRAINS, BUSES]


async def test_429s_in_a_row_wait_longer() -> None:
    """A second 429 straight after the first waits 60 s."""
    guard, _, clock = guarded(
        script={BUSES: [throttled(), throttled(), b'ok']},
    )
    for wait in (0, 30):
        clock.now += wait
        with pytest.raises(FeedThrottled):
            await guard.fetch(feed=BUSES)
    clock.now += 59
    with pytest.raises(FeedBackingOff):
        await guard.fetch(feed=BUSES)
    clock.now += 1
    assert await guard.fetch(feed=BUSES) == b'ok'


async def test_success_resets_the_backoff() -> None:
    """A good answer makes the next 429 wait 30 s again."""
    guard, _, clock = guarded(
        script={BUSES: [throttled(), throttled(), b'ok', throttled(), b'x']},
    )
    for wait in (0, 30, 60):
        clock.now += wait
        try:
            await guard.fetch(feed=BUSES)
        except FeedThrottled:
            pass
    with pytest.raises(FeedThrottled):
        await guard.fetch(feed=BUSES)
    clock.now += 30
    assert await guard.fetch(feed=BUSES) == b'x'


async def test_retry_after_is_honoured() -> None:
    """A Retry-After of 90 s holds the feed for 90 s, not 30."""
    guard, _, clock = guarded(script={BUSES: [throttled(after=90), b'ok']})
    with pytest.raises(FeedThrottled):
        await guard.fetch(feed=BUSES)
    clock.now += 60
    with pytest.raises(FeedBackingOff):
        await guard.fetch(feed=BUSES)
    clock.now += 30
    assert await guard.fetch(feed=BUSES) == b'ok'


@pytest.mark.parametrize(
    'reason', [f'{BUSES}: TimeoutError', f'{BUSES}: HTTP 502'],
)
async def test_outage_backs_off_and_doubles(reason: str) -> None:
    """A timeout or 5xx holds the feed 30 s, then 60 s, until it works."""
    guard, inner, clock = guarded(
        script={
            BUSES: [FeedUnavailable(reason), FeedUnavailable(reason), b'ok'],
        },
    )
    for wait in (0, 30):
        clock.now += wait
        with pytest.raises(FeedUnavailable):
            await guard.fetch(feed=BUSES)
    clock.now += 59
    with pytest.raises(FeedBackingOff, match='backing off'):
        await guard.fetch(feed=BUSES)
    clock.now += 1
    assert await guard.fetch(feed=BUSES) == b'ok'
    assert inner.calls == [BUSES] * 3


async def test_outage_then_429_keeps_doubling() -> None:
    """A 429 straight after a timeout counts as the second strike."""
    guard, _, clock = guarded(
        script={
            BUSES: [FeedUnavailable(f'{BUSES}: TimeoutError'), throttled()],
        },
    )
    with pytest.raises(FeedUnavailable):
        await guard.fetch(feed=BUSES)
    clock.now += 30
    with pytest.raises(FeedThrottled):
        await guard.fetch(feed=BUSES)
    clock.now += 59
    with pytest.raises(FeedBackingOff):
        await guard.fetch(feed=BUSES)


async def test_other_errors_do_not_back_off() -> None:
    """A refusal that isn't a 429, a 5xx or a timeout tries again."""
    guard, inner, _ = guarded(
        script={BUSES: [FeedError(f'{BUSES}: HTTP 403'), b'ok']},
    )
    with pytest.raises(FeedError):
        await guard.fetch(feed=BUSES)
    assert await guard.fetch(feed=BUSES) == b'ok'
    assert inner.calls == [BUSES, BUSES]


async def test_fetches_in_flight_are_shared() -> None:
    """Asking for a feed already on its way waits for the same request."""
    guard, inner, _ = guarded(script={BUSES: [b'one', b'two']})
    inner.gate.clear()
    first = asyncio.create_task(guard.fetch(feed=BUSES))
    second = asyncio.create_task(guard.fetch(feed=BUSES))
    await asyncio.sleep(0)
    inner.gate.set()
    assert [await first, await second] == [b'one', b'one']
    assert inner.calls == [BUSES]
    assert await guard.fetch(feed=BUSES) == b'two'


async def test_shared_429_reaches_every_caller() -> None:
    """Callers sharing a request all see its 429."""
    guard, inner, _ = guarded(script={BUSES: [throttled()]})
    inner.gate.clear()
    calls = [asyncio.create_task(guard.fetch(feed=BUSES)) for _ in range(2)]
    await asyncio.sleep(0)
    inner.gate.set()
    results = await asyncio.gather(*calls, return_exceptions=True)
    assert all(isinstance(result, FeedThrottled) for result in results)
    assert inner.calls == [BUSES]


async def test_close_closes_the_inner_client() -> None:
    """Closing the guard closes the client inside it."""
    guard, inner, _ = guarded(script={})
    await guard.close()
    assert inner.closed
