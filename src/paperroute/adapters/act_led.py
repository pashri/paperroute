"""Hold the Pi's green LED steady once the first frame is drawn."""

import logging
from pathlib import Path

from paperroute.core.device_model import DeviceStatus
from paperroute.core.saved import Settings

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
