"""Observers that collect timings from the running app."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from paperroute.ports import Observer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedTiming:
    """
    Timing of the latest fetch of one feed.

    Attributes
    ----------
    fetch_ms : float
        Download time in milliseconds.
    parse_ms : float
        Parse time in milliseconds.
    trips : int
        Number of trip updates found.
    """

    fetch_ms: float
    parse_ms: float
    trips: int


def _age(*, since: float | None) -> float | None:
    """
    Measure the seconds since a monotonic moment.

    Parameters
    ----------
    since : float | None
        A `time.monotonic()` reading, or None.

    Returns
    -------
    float | None
        Seconds elapsed, or None if there was no reading.
    """
    return None if since is None else time.monotonic() - since


@dataclass
class Timings:
    """
    Keeps the latest timings for the status page.

    Attributes
    ----------
    frames : int
        Frames rendered so far.
    last_render_ms : float | None
        Render time of the latest frame.
    max_render_ms : float | None
        Slowest render so far.
    last_frame_monotonic : float | None
        `time.monotonic()` when the latest frame was drawn.
    feeds : dict[str, FeedTiming]
        Latest timing per feed.
    last_tick_monotonic : float | None
        `time.monotonic()` at the latest app loop tick.
    """

    frames: int = 0
    last_render_ms: float | None = None
    max_render_ms: float | None = None
    last_frame_monotonic: float | None = None
    feeds: dict[str, FeedTiming] = field(default_factory=dict)
    last_tick_monotonic: float | None = None

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Record a rendered frame.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds.
        """
        self.frames += 1
        self.last_render_ms = render_ms
        self.max_render_ms = max(self.max_render_ms or 0.0, render_ms)
        self.last_frame_monotonic = time.monotonic()

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Record a fetched and parsed feed.

        Parameters
        ----------
        feed : str
            Feed path.
        fetch_ms : float
            Download time in milliseconds.
        parse_ms : float
            Parse time in milliseconds.
        trips : int
            Number of trip updates found.
        """
        self.feeds[feed] = FeedTiming(
            fetch_ms=fetch_ms,
            parse_ms=parse_ms,
            trips=trips,
        )

    def ticked(self) -> None:
        """Record an app loop tick."""
        self.last_tick_monotonic = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        """
        Current timings in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            Frame counts and times, ages in seconds (None before the
            first frame or tick) and the latest timing per feed.
        """
        return {
            'frames': self.frames,
            'last_render_ms': self.last_render_ms,
            'max_render_ms': self.max_render_ms,
            'frame_age_s': _age(since=self.last_frame_monotonic),
            'tick_age_s': _age(since=self.last_tick_monotonic),
            'feeds': {
                name: {
                    'fetch_ms': timing.fetch_ms,
                    'parse_ms': timing.parse_ms,
                    'trips': timing.trips,
                }
                for name, timing in self.feeds.items()
            },
        }


class NullObserver:
    """An observer that ignores every event."""

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Ignore a rendered frame.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds.
        """

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Ignore a parsed feed.

        Parameters
        ----------
        feed : str
            Feed path.
        fetch_ms : float
            Download time in milliseconds.
        parse_ms : float
            Parse time in milliseconds.
        trips : int
            Number of trip updates found.
        """

    def ticked(self) -> None:
        """Ignore a tick."""


class ObserverGroup:
    """
    Pass each event to every observer, even if one fails.

    Parameters
    ----------
    observers : Sequence[Observer]
        Observers to notify, in order.
    """

    def __init__(self, *, observers: Sequence[Observer]) -> None:
        self._observers = list(observers)

    def _each(self, *, call: Callable[[Observer], None]) -> None:
        """
        Run a call on every observer, logging any that raise.

        Parameters
        ----------
        call : Callable[[Observer], None]
            Call to make on each observer.
        """
        for observer in self._observers:
            try:
                call(observer)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception('observer failed: %r', observer)

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Pass on a rendered frame.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds.
        """
        self._each(call=lambda o: o.frame_drawn(render_ms=render_ms))

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Pass on a parsed feed.

        Parameters
        ----------
        feed : str
            Feed path.
        fetch_ms : float
            Download time in milliseconds.
        parse_ms : float
            Parse time in milliseconds.
        trips : int
            Number of trip updates found.
        """
        self._each(
            call=lambda o: o.feed_parsed(
                feed=feed,
                fetch_ms=fetch_ms,
                parse_ms=parse_ms,
                trips=trips,
            ),
        )

    def ticked(self) -> None:
        """Pass on a tick."""
        self._each(call=lambda o: o.ticked())
