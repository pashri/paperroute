"""Tell systemd the service is ready and still alive."""

import logging
import os
import socket
from collections.abc import Mapping

from paperroute_device.core.device_model import DeviceStatus
from paperroute_hardware.ports import PanelRefresh

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

    Sends READY=1 on the first tick or frame, and WATCHDOG=1 on every
    tick.
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
        Send READY=1 if no tick has, retrying until it is sent.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds. Unused.
        """
        if not self._ready_sent:
            self._ready_sent = self._send(message=b'READY=1')

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
        """Send READY=1 once, then WATCHDOG=1."""
        if not self._ready_sent:
            self._ready_sent = self._send(message=b'READY=1')
        self._send(message=b'WATCHDOG=1')

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
