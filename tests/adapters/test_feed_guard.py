"""Tests for backing off throttled feeds and sharing fetches in flight."""

import asyncio
from typing import Final

import pytest

from paperroute.adapters.feed_guard import (
    FeedBackingOff,
    GuardedFeeds,
    JoinedBody,
    backoff_s,
)
from paperroute.adapters.tfnsw import (
    FeedError,
    FeedThrottled,
    FeedTimedOut,
    FeedUnavailable,
)
from paperroute.summary import Summary

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
    assert not isinstance(first.result(), JoinedBody)
    assert isinstance(second.result(), JoinedBody)
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


class SlowFeeds(ScriptedFeeds):
    """A scripted client whose every answer takes a set time."""

    def __init__(
        self,
        *,
        script: dict[str, list[bytes | FeedError]],
        clock: Clock,
        takes_s: float,
    ) -> None:
        super().__init__(script=script)
        self.clock = clock
        self.takes_s = takes_s

    async def fetch(self, *, feed: str) -> bytes:
        """
        Move the clock on, then give the scripted answer.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            The scripted body.
        """
        self.clock.now += self.takes_s
        return await super().fetch(feed=feed)


def counted(
    *, script: dict[str, list[bytes | FeedError]], takes_s: float = 0.25,
) -> tuple[GuardedFeeds, Summary, Clock]:
    """
    Wrap a slow scripted client, counting into a summary.

    Parameters
    ----------
    script : dict[str, list[bytes | FeedError]]
        Answers by feed.
    takes_s : float
        Seconds each answer takes.

    Returns
    -------
    tuple[GuardedFeeds, Summary, Clock]
        The guard, its summary and the clock they share.
    """
    clock = Clock()
    inner = SlowFeeds(script=script, clock=clock, takes_s=takes_s)
    summary = Summary(clock=clock)
    guard = GuardedFeeds(inner=inner, clock=clock, summary=summary)
    return guard, summary, clock


def bus_counts(*, summary: Summary) -> dict[str, object]:
    """
    Give the buses feed's counts from a summary.

    Parameters
    ----------
    summary : Summary
        The summary.

    Returns
    -------
    dict[str, object]
        The feed's entry in the report.
    """
    feeds = summary.report()['feeds']
    assert isinstance(feeds, dict)
    entry = feeds[BUSES]
    assert isinstance(entry, dict)
    return entry


async def test_answered_request_is_counted_with_its_time() -> None:
    """A good answer counts as sent and OK, with how long it took."""
    guard, summary, _ = counted(script={BUSES: [b'one']}, takes_s=0.25)
    await guard.fetch(feed=BUSES)
    counts = bus_counts(summary=summary)
    assert (counts['sent'], counts['ok']) == (1, 1)
    assert counts['fetch_ms'] == {'median': 250.0, 'p90': 250.0}


async def test_failed_requests_are_counted_by_reason() -> None:
    """A 429, a timeout, a 5xx and a 403 each count under their reason."""
    guard, summary, clock = counted(
        script={
            BUSES: [
                throttled(),
                FeedTimedOut(f'{BUSES}: TimeoutError'),
                FeedUnavailable(f'{BUSES}: HTTP 502'),
                FeedError(f'{BUSES}: HTTP 403'),
            ],
        },
    )
    for _ in range(4):
        clock.now += 1000
        with pytest.raises(FeedError):
            await guard.fetch(feed=BUSES)
    counts = bus_counts(summary=summary)
    assert (counts['sent'], counts['ok']) == (4, 0)
    assert counts['failed'] == {'429': 1, 'timeout': 1, '5xx': 1, 'other': 1}


async def test_skipped_fetch_counts_as_backed_off_not_sent() -> None:
    """A fetch turned away by the backoff sends nothing and is counted."""
    guard, summary, _ = counted(script={BUSES: [throttled()]})
    with pytest.raises(FeedThrottled):
        await guard.fetch(feed=BUSES)
    with pytest.raises(FeedBackingOff):
        await guard.fetch(feed=BUSES)
    counts = bus_counts(summary=summary)
    assert (counts['sent'], counts['backed_off']) == (1, 1)


async def test_joined_fetch_counts_as_joined_not_sent() -> None:
    """A fetch that shares a request in flight is counted once as joined."""
    guard, summary, _ = counted(script={BUSES: [b'one']})
    assert isinstance(guard.inner, ScriptedFeeds)
    guard.inner.gate.clear()
    first = asyncio.create_task(guard.fetch(feed=BUSES))
    second = asyncio.create_task(guard.fetch(feed=BUSES))
    await asyncio.sleep(0)
    guard.inner.gate.set()
    await asyncio.gather(first, second)
    counts = bus_counts(summary=summary)
    assert (counts['sent'], counts['ok'], counts['joined']) == (1, 1, 1)
