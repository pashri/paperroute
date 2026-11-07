"""A periodic INFO line summing up feed requests and frames drawn."""

import logging
import statistics
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from paperroute.adapters.tfnsw import (
    FeedError,
    FeedThrottled,
    FeedTimedOut,
    FeedUnavailable,
)
from paperroute.observers import NullObserver
from paperroute_hardware.ports import PanelRefresh

SUMMARY_EVERY_S: Final[float] = 900.0
DECILES: Final[int] = 10

logger = logging.getLogger(__name__)


class FailReason(StrEnum):
    """Why a feed request failed."""

    THROTTLED = '429'
    TIMEOUT = 'timeout'
    SERVER = '5xx'
    OTHER = 'other'


def failure_reason(*, error: FeedError) -> FailReason:
    """
    Sort a failed request under its reason.

    Parameters
    ----------
    error : FeedError
        The failure.

    Returns
    -------
    FailReason
        429, timeout, 5xx, or other for anything else.
    """
    match error:
        case FeedThrottled():
            return FailReason.THROTTLED
        case FeedTimedOut():
            return FailReason.TIMEOUT
        case FeedUnavailable():
            return FailReason.SERVER
        case _:
            return FailReason.OTHER


def spread(*, values: list[float]) -> dict[str, float | None]:
    """
    Give the median and 90th percentile of some times.

    Parameters
    ----------
    values : list[float]
        Times in milliseconds.

    Returns
    -------
    dict[str, float | None]
        'median' and 'p90', to one decimal place; None for no values.
    """
    if not values:
        return {'median': None, 'p90': None}
    p90 = (
        statistics.quantiles(values, n=DECILES, method='inclusive')[-1]
        if len(values) > 1
        else values[0]
    )
    return {
        'median': round(statistics.median(values), 1),
        'p90': round(p90, 1),
    }


@dataclass
class FeedTally:
    """
    What one feed's fetches came to in a window.

    Attributes
    ----------
    ok : int
        Requests answered with a body.
    failed : Counter[FailReason]
        Requests that failed, by reason.
    backed_off : int
        Fetches skipped, sending nothing, while the feed backed off.
    joined : int
        Fetches that shared a request already in flight.
    fetch_ms : list[float]
        Time each answered request took.
    """

    ok: int = 0
    failed: Counter[FailReason] = field(default_factory=Counter)
    backed_off: int = 0
    joined: int = 0
    fetch_ms: list[float] = field(default_factory=list)

    def report(self) -> dict[str, object]:
        """
        Give the counts in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            Requests sent, OK, failed by reason, backed off and joined,
            with the median and p90 of the answered requests' times.
        """
        return {
            'sent': self.ok + self.failed.total(),
            'ok': self.ok,
            'failed': {
                reason.value: self.failed[reason] for reason in FailReason
            },
            'backed_off': self.backed_off,
            'joined': self.joined,
            'fetch_ms': spread(values=self.fetch_ms),
        }


class Summary(NullObserver):
    """
    Counts feed requests and frames, logging them every 15 minutes.

    The line is logged at the first app tick after the window ends, and
    the counts then start again.

    Parameters
    ----------
    every_s : float
        Seconds each summary covers.
    clock : Callable[[], float]
        Returns the current time in seconds.
    """

    def __init__(
        self,
        *,
        every_s: float = SUMMARY_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.every_s = every_s
        self.clock = clock
        self.started = clock()
        self.feeds: dict[str, FeedTally] = {}
        self.render_ms: list[float] = []
        self.panel_partial_ms: list[float] = []
        self.panel_full_ms: list[float] = []
        self.panel_partials = 0
        self.panel_fulls = 0
        self.panel_clears = 0

    def tally(self, *, feed: str) -> FeedTally:
        """
        Give a feed's counts, starting them if needed.

        Parameters
        ----------
        feed : str
            Feed path.

        Returns
        -------
        FeedTally
            The feed's counts in this window.
        """
        return self.feeds.setdefault(feed, FeedTally())

    def sent(self, *, feed: str, fetch_ms: float) -> None:
        """
        Count a request that was answered.

        Parameters
        ----------
        feed : str
            Feed path.
        fetch_ms : float
            How long it took, in milliseconds.
        """
        tally = self.tally(feed=feed)
        tally.ok += 1
        tally.fetch_ms.append(fetch_ms)

    def failed(self, *, feed: str, reason: FailReason) -> None:
        """
        Count a request that failed.

        Parameters
        ----------
        feed : str
            Feed path.
        reason : FailReason
            Why.
        """
        self.tally(feed=feed).failed[reason] += 1

    def backed_off(self, *, feed: str) -> None:
        """
        Count a fetch skipped while the feed backs off.

        Parameters
        ----------
        feed : str
            Feed path.
        """
        self.tally(feed=feed).backed_off += 1

    def joined(self, *, feed: str) -> None:
        """
        Count a fetch that shared a request already in flight.

        Parameters
        ----------
        feed : str
            Feed path.
        """
        self.tally(feed=feed).joined += 1

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Count a rendered frame.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds.
        """
        self.render_ms.append(render_ms)

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Keep a good panel refresh's time in milliseconds.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh; a failed one is left out. A clear is counted
            apart from fulls, but its time joins theirs.
        """
        if not refresh.ok:
            return
        if refresh.clear:
            self.panel_clears += 1
        elif refresh.full:
            self.panel_fulls += 1
        else:
            self.panel_partials += 1
        times = self.panel_full_ms if refresh.full else self.panel_partial_ms
        times.append(refresh.seconds * 1000)

    def report(self) -> dict[str, object]:
        """
        Give this window's counts in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            The window's length in seconds, frames drawn with the median
            and p90 render time, the median and p90 of good panel
            patches and full refreshes in milliseconds, how many good
            partial, full (cleaning ones included) and clear refreshes
            there were, and each feed's counts.
        """
        return {
            'window_s': round(self.clock() - self.started, 1),
            'frames': len(self.render_ms),
            'render_ms': spread(values=self.render_ms),
            'panel_partial_ms': spread(values=self.panel_partial_ms),
            'panel_full_ms': spread(values=self.panel_full_ms),
            'panel_partials': self.panel_partials,
            'panel_fulls': self.panel_fulls,
            'panel_clears': self.panel_clears,
            'feeds': {
                feed: tally.report() for feed, tally in self.feeds.items()
            },
        }

    def ticked(self) -> None:
        """Log the summary and start again once the window has ended."""
        if self.clock() - self.started < self.every_s:
            return
        logger.info('summary', extra=self.report())
        self.started = self.clock()
        self.feeds = {}
        self.render_ms = []
        self.panel_partial_ms = []
        self.panel_full_ms = []
        self.panel_partials = 0
        self.panel_fulls = 0
        self.panel_clears = 0
