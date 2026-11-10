"""Observers that collect timings from the running board."""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from paperroute_device.core.device_model import DeviceStatus
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.runtime.timings import KitTimings
from paperroute_device.runtime.timings import ObserverGroup as KitObserverGroup
from paperroute_hardware.ports import PanelRefresh
from paperroute_transit.adapters.settings_form import form_values
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
class Timings(KitTimings):
    """
    Keeps the kit's timings with the feeds, pins and board settings.

    Attributes
    ----------
    feeds : dict[str, FeedTiming]
        Latest timing per feed.
    last_ok : dict[str, float]
        Wall-clock time of each feed's latest parse.
    board : Settings | None
        The settings the board last reported, or None before it does;
        their device part is the kit's `settings`.
    pins : PinsSeen | None
        The pins, or None before the board reports them.
    """

    feeds: dict[str, FeedTiming] = field(default_factory=dict)
    last_ok: dict[str, float] = field(default_factory=dict)
    board: Settings | None = None
    pins: PinsSeen | None = None

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

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Record the settings in force.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self.board = settings

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

    def last_used(self) -> float | None:
        """
        Say when a feed last worked.

        Returns
        -------
        float | None
            The wall-clock time of the latest parse, or None before one.
        """
        return max(self.last_ok.values(), default=None)

    def snapshot(self) -> dict[str, object]:
        """
        Current timings in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            The kit's snapshot with the latest timing per feed, with when
            it last worked, straight after the tick's age, and every
            setting once the board has reported its own.
        """
        now = time.time()
        feeds = {
            name: {
                'fetch_ms': timing.fetch_ms,
                'parse_ms': timing.parse_ms,
                'trips': timing.trips,
                'last_ok': self.last_ok[name],
                'age_s': now - self.last_ok[name],
            }
            for name, timing in self.feeds.items()
        }
        snapshot: dict[str, object] = {}
        for key, value in super().snapshot().items():
            snapshot[key] = value
            if key == 'tick_age_s':
                snapshot['feeds'] = feeds
        if self.settings is not None and self.board is not None:
            settings = replace(self.board, device=self.settings)
            snapshot['settings'] = form_values(settings=settings)
        return snapshot


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

    def work_done(self, *, name: str, ms: float) -> None:
        """
        Ignore a piece of background work.

        Parameters
        ----------
        name : str
            What was done.
        ms : float
            Time taken, in milliseconds.
        """

    def app_seen(self, *, status: Mapping[str, object]) -> None:
        """
        Ignore the app's status.

        Parameters
        ----------
        status : Mapping[str, object]
            The status.
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

    def device_settings_seen(self, *, settings: DeviceSettings) -> None:
        """
        Ignore the device settings.

        Parameters
        ----------
        settings : DeviceSettings
            The settings.
        """

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Ignore a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh.
        """


class ObserverGroup(KitObserverGroup):
    """
    Pass each event on to every observer, even if one fails.

    Parameters
    ----------
    observers : Sequence[Observer]
        Observers to notify, in order; they also hear the parsed feeds,
        the settings and the pins.
    """

    def __init__(self, *, observers: Sequence[Observer]) -> None:
        super().__init__(observers=observers)
        self._board = list(observers)

    def _tell(self, *, call: Callable[[Observer], None]) -> None:
        """
        Run a call on each observer, logging any that raise.

        Parameters
        ----------
        call : Callable[[Observer], None]
            Call to make on each observer.
        """
        for observer in self._board:
            try:
                call(observer)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception('observer failed: %r', observer)

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
        self._tell(
            call=lambda o: o.feed_parsed(
                feed=feed,
                fetch_ms=fetch_ms,
                parse_ms=parse_ms,
                trips=trips,
            ),
        )

    def settings_seen(self, *, settings: Settings) -> None:
        """
        Pass on the settings.

        Parameters
        ----------
        settings : Settings
            The settings.
        """
        self._tell(call=lambda o: o.settings_seen(settings=settings))

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
        self._tell(call=lambda o: o.pins_seen(pins=pins, shown=shown))
