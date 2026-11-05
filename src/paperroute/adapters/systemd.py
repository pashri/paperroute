"""Tell systemd the service is ready and still alive."""

import logging
import os
import socket

from paperroute.core.device_model import DeviceStatus
from paperroute.core.model import Pin
from paperroute.core.saved import Settings
from paperroute.ports import PanelRefresh

logger = logging.getLogger(__name__)


def notify_address(*, path: str) -> str:
    """
    Turn a NOTIFY_SOCKET value into a socket address.

    Parameters
    ----------
    path : str
        Socket path. A leading '@' means an abstract-namespace socket.

    Returns
    -------
    str
        The path, with a leading '@' replaced by a NUL character.
    """
    return '\0' + path[1:] if path.startswith('@') else path



def send_datagram(*, address: str, message: bytes) -> None:
    """
    Send one datagram to a Unix socket without blocking.

    Parameters
    ----------
    address : str
        The socket's address.
    message : bytes
        The message.

    Raises
    ------
    OSError
        When it can't be sent, including when the queue is full.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        sock.sendto(message, address)

class SystemdNotifier:
    """
    Sends sd_notify messages over the notify socket.

    Sends READY=1 after the first frame and WATCHDOG=1 on every tick.
    With no socket path it does nothing.

    Parameters
    ----------
    socket_path : str | None
        Notify socket path. A leading '@' means an abstract-namespace
        socket. None disables the notifier.
    """

    def __init__(self, *, socket_path: str | None) -> None:
        self.socket_path = socket_path
        self._ready_sent = False
        self._failing = False

    @classmethod
    def from_environment(cls) -> 'SystemdNotifier':
        """
        Build a notifier from the NOTIFY_SOCKET variable.

        Returns
        -------
        SystemdNotifier
            Notifier for that socket, or a silent one if it is unset.
        """
        return cls(socket_path=os.environ.get('NOTIFY_SOCKET') or None)

    def _send(self, *, message: bytes) -> bool:
        """
        Send one datagram.

        The send never blocks: a full socket queue counts as a failure.
        A failure is logged once per outage, not on every attempt.

        Parameters
        ----------
        message : bytes
            Message such as b'READY=1'.

        Returns
        -------
        bool
            True if the message was sent.
        """
        if self.socket_path is None:
            return False
        try:
            send_datagram(
                address=notify_address(path=self.socket_path), message=message,
            )
        except OSError as error:
            if not self._failing:
                logger.warning('systemd notify failed: %s', error)
            self._failing = True
            return False
        self._failing = False
        return True

    def frame_drawn(
        self, *, render_ms: float  # pylint: disable=unused-argument
    ) -> None:
        """
        Send READY=1 on the first frame, retrying until it is sent.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds. Unused.
        """
        if not self._ready_sent:
            self._ready_sent = self._send(message=b'READY=1')

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
        """Send WATCHDOG=1."""
        self._send(message=b'WATCHDOG=1')

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
