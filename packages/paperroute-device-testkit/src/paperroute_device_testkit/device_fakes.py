"""Hand-written stand-ins for the device kit's ports."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from paperroute_device.core.device_model import (
    DeviceFacts,
    JoinOutcome,
    JoinRequest,
    PowerAction,
    ResetKind,
    WifiNetwork,
    WifiStatus,
)
from paperroute_device_testkit.hardware_fakes import FrameRecorder


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


@dataclass(frozen=True)
class FixedClock:
    """A clock stuck at one moment."""

    at: datetime

    def now(self) -> datetime:
        """
        The fixed time.

        Returns
        -------
        datetime
            Time.
        """
        return self.at

    def synced(self) -> bool:
        """
        Say the clock is synchronised.

        Returns
        -------
        bool
            Always True.
        """
        return True


@dataclass
class SyncingClock:
    """
    A clock stuck at one moment that syncs after a few asks.

    Attributes
    ----------
    at : datetime
        The fixed time.
    unsynced : int
        How many asks are answered unsynchronised first.
    """

    at: datetime
    unsynced: int

    def now(self) -> datetime:
        """
        The fixed time.

        Returns
        -------
        datetime
            Time.
        """
        return self.at

    def synced(self) -> bool:
        """
        Say whether the clock is synchronised, counting down the asks.

        Returns
        -------
        bool
            False for the first `unsynced` asks, then True.
        """
        self.unsynced -= 1
        return self.unsynced < 0


@dataclass
class FakePower:
    """
    A power control that answers at once and notes the screen when called.

    Attributes
    ----------
    result : bool
        What each action returns.
    display : FrameRecorder | None
        The display whose frame count is noted at each call.
    calls : list[tuple[PowerAction, int]]
        Each action asked for, with how many frames were shown by then.
    """

    result: bool = True
    display: FrameRecorder | None = None
    calls: list[tuple[PowerAction, int]] = field(default_factory=list)

    async def control(self, *, action: PowerAction) -> bool:
        """
        Note the call and give the set result.

        Parameters
        ----------
        action : PowerAction
            The action asked for.

        Returns
        -------
        bool
            `result`.
        """
        shown = 0 if self.display is None else len(self.display.frames)
        self.calls.append((action, shown))
        return self.result


def device_facts(*, missing_keys: tuple[str, ...] = ()) -> DeviceFacts:
    """
    Make facts for a device on no network.

    Parameters
    ----------
    missing_keys : tuple[str, ...]
        The display names of the keys not set.

    Returns
    -------
    DeviceFacts
        The facts.
    """
    return DeviceFacts(
        wifi=WifiStatus(),
        clock_synced=True,
        uptime_s=60.0,
        cpu_c=45.0,
        disk_free=1_000_000,
        mem_free=2_000_000,
        version='dev',
        last_error=None,
        missing_keys=missing_keys,
    )


@dataclass
class FakeProbe:
    """
    A device probe that gives fixed facts, or raises.

    Attributes
    ----------
    facts : DeviceFacts
        What `probe` gives.
    error : Exception | None
        Raised by `probe` when set.
    """

    facts: DeviceFacts = field(default_factory=device_facts)
    error: Exception | None = None

    async def probe(self) -> DeviceFacts:
        """
        Give the fixed facts.

        Returns
        -------
        DeviceFacts
            `facts`.

        Raises
        ------
        Exception
            `error`, when set.
        """
        if self.error is not None:
            raise self.error
        return self.facts


@dataclass
class HangingProbe:
    """A device probe that never answers."""

    async def probe(self) -> DeviceFacts:
        """
        Wait for ever.

        Returns
        -------
        DeviceFacts
            Never.
        """
        await asyncio.Event().wait()
        return device_facts()


@dataclass
class RecordingResets:
    """
    Resets that note each kind asked for and touch nothing.

    Attributes
    ----------
    kinds : list[ResetKind]
        Each reset asked for.
    """

    kinds: list[ResetKind] = field(default_factory=list)

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Note the reset.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.
        """
        self.kinds.append(kind)
