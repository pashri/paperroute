"""Tell systemd the service is ready and still alive."""

import logging
import os
import socket

logger = logging.getLogger(__name__)


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

    def _send(self, *, message: bytes) -> None:
        """
        Send one datagram, logging a warning if that fails.

        Parameters
        ----------
        message : bytes
            Message such as b'READY=1'.
        """
        if self.socket_path is None:
            return
        address = self.socket_path
        if address.startswith('@'):
            address = '\0' + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.sendto(message, address)
        except OSError as error:
            logger.warning('systemd notify failed: %s', error)

    def frame_drawn(
        self, *, render_ms: float  # pylint: disable=unused-argument
    ) -> None:
        """
        Send READY=1 on the first frame only.

        Parameters
        ----------
        render_ms : float
            Render time in milliseconds. Unused.
        """
        if not self._ready_sent:
            self._ready_sent = True
            self._send(message=b'READY=1')

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
