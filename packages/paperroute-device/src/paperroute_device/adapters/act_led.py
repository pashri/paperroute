"""Hold the Pi's green LED steady once the first frame is drawn."""

import logging
from collections.abc import Mapping
from pathlib import Path

from paperroute_device.core.device_model import DeviceStatus
from paperroute_hardware.ports import PanelRefresh

logger = logging.getLogger(__name__)


class ActLed:
    """
    Sets the green LED to steady on after the first frame.

    Only the `trigger` file is written. If it is missing or cannot be
    written, one warning is logged and no further attempts are made.

    Parameters
    ----------
    root : Path
        The LED's sysfs directory.
    """

    def __init__(
        self, *, root: Path = Path('/sys/class/leds/ACT')
    ) -> None:
        self._root = root
        self._done = False

    def frame_drawn(
        self, *, render_ms: float  # pylint: disable=unused-argument
    ) -> None:
        """
        Write default-on to the trigger on the first frame only.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds. Unused.
        """
        if self._done:
            return
        self._done = True
        try:
            (self._root / 'trigger').write_text('default-on')
        except OSError as error:
            logger.warning('cannot set LED trigger: %s', error)

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
            The app's readings.
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

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Ignore a panel refresh.

        Parameters
        ----------
        refresh : PanelRefresh
            The refresh.
        """
