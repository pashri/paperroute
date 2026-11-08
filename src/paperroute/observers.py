"""Observers that collect timings from the running app."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field

from paperroute.adapters.settings_form import form_values
from paperroute_device.core.device_model import DeviceStatus
from paperroute_device.ports import DeviceObserver
from paperroute_hardware.ports import PanelRefresh, PanelTimes
from paperroute_transit.core.model import Pin
from paperroute_transit.core.saved import Settings
from paperroute_transit.ports import Observer

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


@dataclass(frozen=True)
class PinsSeen:
    """
    The pins as the app last reported them.

    Attributes
    ----------
    pins : tuple[Pin, ...]
        The pins, in knob order.
    shown : int | None
        Index of the pin on screen, or None when the unpinned place is.
    revision : int
        How many times the pins have been reported; 1 for the first.
    """

    pins: tuple[Pin, ...]
    shown: int | None
    revision: int


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
    pins : PinsSeen | None
        The pins, or None before the app reports them.
    panel : PanelTimes | None
        The panel's refresh times, or None when there is no panel.
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
    pins: PinsSeen | None = None
    panel: PanelTimes | None = None

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

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Record the pins under the next revision.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            Index of the pin on screen, or None.
        """
        revision = 1 if self.pins is None else self.pins.revision + 1
        self.pins = PinsSeen(pins=pins, shown=shown, revision=revision)

    def knobs_swapped(self) -> bool:
        """
        Say whether the settings in force swap the knobs.

        Returns
        -------
        bool
            The swap switch; False before the app reports its settings.
        """
        settings = self.settings
        return settings is not None and settings.device.swap_knobs

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Record a panel refresh, starting the panel's times if needed.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh; a failed one only marks the panel not ok.
        """
        if self.panel is None:
            self.panel = PanelTimes()
        panel = self.panel
        panel.ok = refresh.ok
        if not refresh.ok:
            return
        if refresh.full:
            panel.full_s = refresh.seconds
        else:
            panel.partial_s = refresh.seconds

    def snapshot(self) -> dict[str, object]:
        """
        Current timings in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            Frame counts and times, ages in seconds (None before the
            first frame or tick), the latest timing per feed with when
            it last worked, the panel's refresh times (None when there
            is no panel), and the settings and device status once
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
            'panel': None if self.panel is None else asdict(self.panel),
        }
        if self.settings is not None:
            snapshot['settings'] = form_values(settings=self.settings)
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

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Ignore the pins.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            Index of the pin on screen, or None.
        """

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Ignore a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh.
        """


class ObserverGroup:
    """
    Pass each event on to every observer, even if one fails.

    The device events go to the observers first, then the devices. The
    settings and pins go to the observers only.

    Parameters
    ----------
    observers : Sequence[Observer]
        Observers to notify, in order.
    devices : Sequence[DeviceObserver]
        Device observers to notify after them, in order.
    """

    def __init__(
        self,
        *,
        observers: Sequence[Observer],
        devices: Sequence[DeviceObserver] = (),
    ) -> None:
        self._observers = list(observers)
        self._everyone: list[DeviceObserver] = [*observers, *devices]

    @staticmethod
    def _each[T: DeviceObserver](
        *,
        observers: Sequence[T],
        call: Callable[[T], None],
    ) -> None:
        """
        Run a call on each observer, logging any that raise.

        Parameters
        ----------
        observers : Sequence[T]
            Observers to call, in order.
        call : Callable[[T], None]
            Call to make on each observer.
        """
        for observer in observers:
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
        self._each(
            observers=self._everyone,
            call=lambda o: o.frame_drawn(render_ms=render_ms),
        )

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
            observers=self._everyone,
            call=lambda o: o.feed_parsed(
                feed=feed,
                fetch_ms=fetch_ms,
                parse_ms=parse_ms,
                trips=trips,
            ),
        )

    def ticked(self) -> None:
        """Pass on a tick."""
        self._each(
            observers=self._everyone,
            call=lambda o: o.ticked(),
        )

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Pass on the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self._each(
            observers=self._observers,
            call=lambda o: o.settings_seen(settings=settings),
        )

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Pass on the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self._each(
            observers=self._everyone,
            call=lambda o: o.status_seen(status=status),
        )

    def pins_seen(self, *, pins: tuple[Pin, ...], shown: int | None) -> None:
        """
        Pass on the pins.

        Parameters
        ----------
        pins : tuple[Pin, ...]
            The pins.
        shown : int | None
            Index of the pin on screen, or None.
        """
        self._each(
            observers=self._observers,
            call=lambda o: o.pins_seen(pins=pins, shown=shown),
        )

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Pass on a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh.
        """
        self._each(
            observers=self._everyone,
            call=lambda o: o.panel_refreshed(refresh=refresh),
        )
