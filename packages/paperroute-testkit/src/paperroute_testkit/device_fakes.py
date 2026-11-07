"""Hand-written stand-ins for the device kit's ports."""

from dataclasses import dataclass, field

from paperroute_device.core.device_model import (
    JoinOutcome,
    JoinRequest,
    WifiNetwork,
    WifiStatus,
)


@dataclass
# pylint: disable-next=too-many-instance-attributes
class FakeWifi:
    """
    A Wi-Fi control with fixed answers that records what it was asked.

    Attributes
    ----------
    link : WifiStatus
        What `status` reports.
    forgotten : int
        How many times `forget_all` was called.
    networks : tuple[WifiNetwork, ...]
        What `scan` finds.
    hotspot_ok : bool
        What `start_hotspot` answers.
    outcome : JoinOutcome
        What `join` answers.
    calls : list[str]
        Each action asked for, in order.
    passwords : list[str]
        Each hotspot password started with.
    joined : list[JoinRequest]
        Each join asked for.
    active : str | None
        What `active_wifi` answers.
    saved : bool
        What `hotspot_saved` answers.
    """

    link: WifiStatus = field(default_factory=WifiStatus)
    forgotten: int = 0
    networks: tuple[WifiNetwork, ...] = ()
    hotspot_ok: bool = False
    outcome: JoinOutcome = JoinOutcome.FAILED
    calls: list[str] = field(default_factory=list)
    passwords: list[str] = field(default_factory=list)
    joined: list[JoinRequest] = field(default_factory=list)
    active: str | None = None
    saved: bool = False

    async def status(self) -> WifiStatus:
        """
        Give the fixed link.

        Returns
        -------
        WifiStatus
            `link`.
        """
        return self.link

    async def scan(self) -> tuple[WifiNetwork, ...]:
        """
        Find the fixed networks.

        Returns
        -------
        tuple[WifiNetwork, ...]
            `networks`.
        """
        self.calls.append('scan')
        return self.networks

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Record the start and give the fixed answer.

        Parameters
        ----------
        password : str
            Hotspot password; recorded.

        Returns
        -------
        bool
            `hotspot_ok`.
        """
        self.calls.append('start')
        self.passwords.append(password)
        return self.hotspot_ok

    async def stop_hotspot(self) -> None:
        """Record the stop."""
        self.calls.append('stop')

    async def join(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Record the join and give the fixed outcome.

        Parameters
        ----------
        request : JoinRequest
            Network asked for; recorded.

        Returns
        -------
        JoinOutcome
            `outcome`.
        """
        self.calls.append('join')
        self.joined.append(request)
        return self.outcome

    async def forget_all(self) -> None:
        """Count the call."""
        self.forgotten += 1

    async def active_wifi(self) -> str | None:
        """
        Record the call and give the fixed connection.

        Returns
        -------
        str | None
            `active`.
        """
        self.calls.append('active')
        return self.active

    async def reconnect(self, *, uuid: str) -> None:
        """
        Record the reconnect.

        Parameters
        ----------
        uuid : str
            The connection; recorded.
        """
        self.calls.append(f'up {uuid}')

    async def hotspot_saved(self) -> bool:
        """
        Record the call and give the fixed answer.

        Returns
        -------
        bool
            `saved`.
        """
        self.calls.append('saved')
        return self.saved



@dataclass
class FakeKeys:
    """
    A key store holding a key or not.

    Attributes
    ----------
    key : str | None
        The key held.
    """

    key: str | None = field(default=None, repr=False)

    def read(self) -> str | None:
        """
        Give the key.

        Returns
        -------
        str | None
            `key`.
        """
        return self.key

    def present(self) -> bool:
        """
        Say whether a key is held.

        Returns
        -------
        bool
            True when `key` is set.
        """
        return self.key is not None

    def write(self, *, key: str) -> None:
        """
        Hold a key.

        Parameters
        ----------
        key : str
            The key.
        """
        self.key = key

    def clear(self) -> None:
        """Drop the key."""
        self.key = None

