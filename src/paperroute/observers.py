"""Observers that collect timings from the running app."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from paperroute.core.device_model import DeviceStatus
from paperroute.core.saved import Settings
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
    settings : Settings | None
        The settings in force, or None before the app reports them.
    last_ok : dict[str, float]
        Wall-clock time of each feed's latest parse.
    device : DeviceStatus | None
        The device status, or None before the app reports it.
    """

    frames: int = 0
    last_render_ms: float | None = None
    max_render_ms: float | None = None
    last_frame_monotonic: float | None = None
    feeds: dict[str, FeedTiming] = field(default_factory=dict)
    last_tick_monotonic: float | None = None
    settings: Settings | None = None
    last_ok: dict[str, float] = field(default_factory=dict)
    device: DeviceStatus | None = None

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
        self.last_ok[feed] = time.time()

    def ticked(self) -> None:
        """Record an app loop tick."""
        self.last_tick_monotonic = time.monotonic()

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Record the settings in force.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self.settings = settings

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Record the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self.device = status

    def snapshot(self) -> dict[str, object]:
        """
        Current timings in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            Frame counts and times, ages in seconds (None before the
            first frame or tick), the latest timing per feed with when
            it last worked, and the settings and device status once
            reported. The device facts are left out.
        """
        now = time.time()
        snapshot: dict[str, object] = {
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
                    'last_ok': self.last_ok[name],
                    'age_s': now - self.last_ok[name],
                }
                for name, timing in self.feeds.items()
            },
        }
        if self.settings is not None:
            snapshot['settings'] = {
                'auto_rebuild': self.settings.auto_rebuild,
            }
        if self.device is not None:
            snapshot['device'] = device_snapshot(status=self.device)
        return snapshot


def device_snapshot(*, status: DeviceStatus) -> dict[str, object]:
    """
    Give the device status in a JSON-ready form, without its facts.

    Parameters
    ----------
    status : DeviceStatus
        The status.

    Returns
    -------
    dict[str, object]
        Timetable, schema, rebuild state, calls today, the key state and
        what a phone asked for that waits on the panel, and Wi-Fi
        setup's phase; never its password.
    """
    setup = status.setup
    return {
        'timetable': status.timetable,
        'schema': status.schema,
        'rebuilding': status.rebuilding,
        'rebuild_failed': status.rebuild_failed,
        'calls_today': status.calls_today,
        'key_set': status.key_set,
        'asked': None if status.asked is None else status.asked.value,
        'setup_phase': None if setup is None else setup.phase.value,
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

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Ignore the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Ignore the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """


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

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Pass on the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self._each(call=lambda o: o.settings_seen(settings=settings))

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Pass on the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self._each(call=lambda o: o.status_seen(status=status))
