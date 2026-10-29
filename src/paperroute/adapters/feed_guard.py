"""Back off feeds TfNSW throttles or fails, and share fetches in flight."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from paperroute.adapters.tfnsw import FeedError, FeedThrottled, FeedUnavailable
from paperroute.ports import FeedsClient
from paperroute.summary import Summary, failure_reason

FIRST_BACKOFF_S: Final[float] = 30.0
MOST_BACKOFF_S: Final[float] = 300.0
MS_PER_SECOND: Final[float] = 1000.0

logger = logging.getLogger(__name__)


class FeedBackingOff(FeedError):
    """A feed was not asked for because it is backing off."""


class JoinedBody(bytes):
    """A feed body that came from a request another caller started."""


@dataclass(frozen=True)
class Backoff:
    """
    How long a throttled feed waits.

    Attributes
    ----------
    until : float
        Clock time before which the feed is not asked for.
    strikes : int
        429s, timeouts and 5xx answers in a row.
    """

    until: float
    strikes: int


def backoff_s(*, strikes: int, retry_after_s: float | None) -> float:
    """
    Give how long to leave a feed after a 429, a timeout or a 5xx.

    Parameters
    ----------
    strikes : int
        Such failures in a row, this one included.
    retry_after_s : float | None
        Seconds the server asked for, if it said.

    Returns
    -------
    float
        `FIRST_BACKOFF_S` doubled for each earlier strike, or the
        server's Retry-After if that is longer, at most `MOST_BACKOFF_S`.
    """
    doubled = FIRST_BACKOFF_S * 2.0 ** (strikes - 1)
    asked = retry_after_s or 0.0
    return min(max(doubled, asked), MOST_BACKOFF_S)


class GuardedFeeds:
    """
    Wraps a feeds client so a throttled or failing feed backs off alone.

    A feed in its backoff raises `FeedBackingOff` at once, sending
    nothing; other feeds are not held up. A fetch for a feed already on
    its way waits for that request instead of sending another.

    Parameters
    ----------
    inner : FeedsClient
        The client that makes the requests.
    clock : Callable[[], float]
        Returns the current time in seconds.
    summary : Summary | None
        Counts each request, skip and join; a new one by default.
    """

    def __init__(
        self,
        *,
        inner: FeedsClient,
        clock: Callable[[], float] = time.monotonic,
        summary: Summary | None = None,
    ) -> None:
        self.inner = inner
        self.clock = clock
        self.summary = summary or Summary(clock=clock)
        self.backoffs: dict[str, Backoff] = {}
        self.flights: dict[str, asyncio.Task[bytes]] = {}

    async def fetch(self, *, feed: str) -> bytes:
        """
        Download one feed unless it is backing off.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            Protobuf body; a `JoinedBody` when it came from a request
            already in flight.

        Raises
        ------
        FeedBackingOff
            While the feed backs off.
        """
        backoff = self.backoffs.get(feed)
        if backoff is not None and self.clock() < backoff.until:
            self.summary.backed_off(feed=feed)
            raise FeedBackingOff(f'{feed}: backing off')
        flight = self.flights.get(feed)
        if flight is not None:
            self.summary.joined(feed=feed)
            return JoinedBody(await asyncio.shield(flight))
        flight = asyncio.create_task(self.fetched(feed=feed))
        self.flights[feed] = flight
        return await asyncio.shield(flight)

    async def fetched(self, *, feed: str) -> bytes:
        """
        Download a feed, noting a failure or clearing its backoff.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            Protobuf body.
        """
        try:
            data = await self.counted(feed=feed)
        except FeedThrottled as error:
            self.struck(feed=feed, retry_after_s=error.retry_after_s)
            raise
        except FeedUnavailable:
            self.struck(feed=feed, retry_after_s=None)
            raise
        finally:
            del self.flights[feed]
        self.backoffs.pop(feed, None)
        return data

    async def counted(self, *, feed: str) -> bytes:
        """
        Download a feed, counting the request in the summary.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        bytes
            Protobuf body.
        """
        started = self.clock()
        try:
            data = await self.inner.fetch(feed=feed)
        except FeedError as error:
            self.summary.failed(feed=feed, reason=failure_reason(error=error))
            raise
        fetch_ms = (self.clock() - started) * MS_PER_SECOND
        self.summary.sent(feed=feed, fetch_ms=fetch_ms)
        return data

    def struck(self, *, feed: str, retry_after_s: float | None) -> None:
        """
        Start or lengthen a feed's backoff.

        Parameters
        ----------
        feed : str
            Feed path.
        retry_after_s : float | None
            Seconds the server asked for, if it said.
        """
        before = self.backoffs.get(feed)
        strikes = 1 if before is None else before.strikes + 1
        wait = backoff_s(strikes=strikes, retry_after_s=retry_after_s)
        self.backoffs[feed] = Backoff(
            until=self.clock() + wait, strikes=strikes,
        )
        logger.warning(
            'feed backing off',
            extra={'feed': feed, 'seconds': wait, 'strikes': strikes},
        )

    async def close(self) -> None:
        """Close the client inside."""
        await self.inner.close()
