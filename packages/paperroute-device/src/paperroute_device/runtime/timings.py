"""Observers that collect the running board's timings and status."""

# An app package keeps a twin of these observers until it switches over.
# pylint: disable=duplicate-code

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from paperroute_device.adapters.device_form import device_values
from paperroute_device.core.device_model import DeviceStatus
from paperroute_device.core.device_settings import DeviceSettings
from paperroute_device.ports import DeviceObserver
from paperroute_hardware.ports import PanelRefresh, PanelTimes

logger = logging.getLogger(__name__)


def age(*, since: float | None) -> float | None:
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
# pylint: disable-next=too-many-instance-attributes
class KitTimings:
    """
    Keeps the latest timings and status for the status page.

    An app extends it with its own readings, and its `snapshot` adds
    them to this one's.

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
    last_tick_monotonic : float | None
        `time.monotonic()` at the latest loop tick.
    device : DeviceStatus | None
        The device status, or None before the loop reports it.
    settings : DeviceSettings | None
        The device settings in force, or None before they are reported.
    app : dict[str, object]
        The app's status readings; empty before the loop reports them.
    panel : PanelTimes | None
        The panel's refresh times, or None when there is no panel.
    """

    frames: int = 0
    last_render_ms: float | None = None
    max_render_ms: float | None = None
    last_frame_monotonic: float | None = None
    last_tick_monotonic: float | None = None
    device: DeviceStatus | None = None
    settings: DeviceSettings | None = None
    app: dict[str, object] = field(default_factory=dict)
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

    def ticked(self) -> None:
        """Record a loop tick."""
        self.last_tick_monotonic = time.monotonic()

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Record the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self.device = status

    def device_settings_seen(self, *, settings: DeviceSettings) -> None:
        """
        Record the device settings in force.

        Parameters
        ----------
        settings : DeviceSettings
            The settings.
        """
        self.settings = settings

    def app_seen(self, *, status: Mapping[str, object]) -> None:
        """
        Record the app's status.

        Parameters
        ----------
        status : Mapping[str, object]
            The status.
        """
        self.app = dict(status)

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

    def knobs_swapped(self) -> bool:
        """
        Say whether the device settings in force swap the knobs.

        Returns
        -------
        bool
            The swap switch; False before the settings are reported.
        """
        settings = self.settings
        return settings is not None and settings.swap_knobs

    def snapshot(self) -> dict[str, object]:
        """
        Current timings in a JSON-ready form.

        Returns
        -------
        dict[str, object]
            Frame counts and times, ages in seconds (None before the
            first frame or tick), the panel's refresh times (None when
            there is no panel), and the device settings and device
            status once reported. The device facts are left out.
        """
        snapshot: dict[str, object] = {
            'frames': self.frames,
            'last_render_ms': self.last_render_ms,
            'max_render_ms': self.max_render_ms,
            'frame_age_s': age(since=self.last_frame_monotonic),
            'tick_age_s': age(since=self.last_tick_monotonic),
            'panel': None if self.panel is None else asdict(self.panel),
        }
        if self.settings is not None:
            snapshot['settings'] = device_values(settings=self.settings)
        if self.device is not None:
            snapshot['device'] = device_snapshot(
                status=self.device, app=self.app,
            )
        return snapshot


def device_snapshot(
    *, status: DeviceStatus, app: Mapping[str, object],
) -> dict[str, object]:
    """
    Give the device status in a JSON-ready form, without its facts.

    Parameters
    ----------
    status : DeviceStatus
        The status.
    app : Mapping[str, object]
        The app's status readings.

    Returns
    -------
    dict[str, object]
        The app's readings first, then whether every key is set (None
        until known), what a phone asked for that waits on the panel,
        and Wi-Fi setup's phase; never its password and never a key's
        name or value.
    """
    setup = status.setup
    return {
        **app,
        'key_set': (
            None
            if status.missing_keys is None
            else not status.missing_keys
        ),
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

    def ticked(self) -> None:
        """Ignore a tick."""

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Ignore the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """

    def device_settings_seen(self, *, settings: DeviceSettings) -> None:
        """
        Ignore the device settings.

        Parameters
        ----------
        settings : DeviceSettings
            The settings.
        """

    def app_seen(self, *, status: Mapping[str, object]) -> None:
        """
        Ignore the app's status.

        Parameters
        ----------
        status : Mapping[str, object]
            The status.
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

    Parameters
    ----------
    observers : Sequence[DeviceObserver]
        Observers to notify, in order.
    """

    def __init__(self, *, observers: Sequence[DeviceObserver]) -> None:
        self._observers = list(observers)

    def _each(self, *, call: Callable[[DeviceObserver], None]) -> None:
        """
        Run a call on each observer, logging any that raise.

        Parameters
        ----------
        call : Callable[[DeviceObserver], None]
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

    def work_done(self, *, name: str, ms: float) -> None:
        """
        Pass on a piece of background work.

        Parameters
        ----------
        name : str
            What was done.
        ms : float
            Time taken, in milliseconds.
        """
        self._each(call=lambda o: o.work_done(name=name, ms=ms))

    def ticked(self) -> None:
        """Pass on a tick."""
        self._each(call=lambda o: o.ticked())

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Pass on the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status.
        """
        self._each(call=lambda o: o.status_seen(status=status))

    def device_settings_seen(self, *, settings: DeviceSettings) -> None:
        """
        Pass on the device settings.

        Parameters
        ----------
        settings : DeviceSettings
            The settings.
        """
        self._each(
            call=lambda o: o.device_settings_seen(settings=settings),
        )

    def app_seen(self, *, status: Mapping[str, object]) -> None:
        """
        Pass on the app's status.

        Parameters
        ----------
        status : Mapping[str, object]
            The status.
        """
        self._each(call=lambda o: o.app_seen(status=status))

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Pass on a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh.
        """
        self._each(call=lambda o: o.panel_refreshed(refresh=refresh))
