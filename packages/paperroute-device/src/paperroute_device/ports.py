"""Interfaces the device kit depends on; adapters implement them."""

from datetime import datetime
from typing import Protocol

from paperroute_device.core.device_model import (
    DeviceFacts,
    DeviceStatus,
    JoinOutcome,
    JoinRequest,
    PowerAction,
    ResetKind,
    WifiNetwork,
    WifiStatus,
)
from paperroute_hardware.ports import PanelRefresh


class DeviceObserver(Protocol):
    """Receives timing events from the running app."""

    def frame_drawn(self, *, render_ms: float) -> None:
        """
        Record that a frame was rendered.

        Parameters
        ----------
        render_ms : float
            Time taken to render, in milliseconds.
        """

    def feed_parsed(
        self, *, feed: str, fetch_ms: float, parse_ms: float, trips: int
    ) -> None:
        """
        Record that a realtime feed was fetched and parsed.

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
        """Record that the app loop ran once."""

    def status_seen(self, *, status: DeviceStatus) -> None:
        """
        Record the device status.

        Parameters
        ----------
        status : DeviceStatus
            The status, at start and after each change.
        """

    def panel_refreshed(self, *, refresh: PanelRefresh) -> None:
        """
        Record an attempt to refresh the e-paper panel.

        Parameters
        ----------
        refresh : PanelRefresh
            The attempt.
        """


class PowerControl(Protocol):
    """Shuts the device down or restarts it."""

    async def control(self, *, action: PowerAction) -> bool:
        """
        Carry out a power action.

        Parameters
        ----------
        action : PowerAction
            Shut down or restart.

        Returns
        -------
        bool
            True when the action was accepted.
        """


class WifiControl(Protocol):
    """Scans for, joins and shares Wi-Fi."""

    async def status(self) -> WifiStatus:
        """
        Report the Wi-Fi link.

        Returns
        -------
        WifiStatus
            The joined network, signal and address; not managed when
            Wi-Fi can't be controlled.
        """

    async def scan(self) -> tuple[WifiNetwork, ...]:
        """
        List the networks in range.

        Returns
        -------
        tuple[WifiNetwork, ...]
            Strongest first.
        """

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Start the setup hotspot.

        Parameters
        ----------
        password : str
            WPA2 password for the hotspot.

        Returns
        -------
        bool
            True when the hotspot started.
        """

    async def stop_hotspot(self) -> None:
        """Stop the setup hotspot and remove its profile."""

    async def join(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Join a network, keeping its profile only when it worked.

        Parameters
        ----------
        request : JoinRequest
            Network name, password and whether it is hidden.

        Returns
        -------
        JoinOutcome
            How the attempt ended.
        """

    async def forget_all(self) -> None:
        """Delete every saved Wi-Fi profile."""

    async def active_wifi(self) -> str | None:
        """
        Find the Wi-Fi connection in use.

        Returns
        -------
        str | None
            Its UUID, or None with none in use.
        """

    async def reconnect(self, *, uuid: str) -> None:
        """
        Bring a saved connection up again.

        Parameters
        ----------
        uuid : str
            The connection's UUID.
        """

    async def hotspot_saved(self) -> bool:
        """
        Say whether a setup hotspot profile exists.

        Returns
        -------
        bool
            True when one does.
        """


class Resets(Protocol):
    """Resets the settings or erases everything."""

    async def reset(self, *, kind: ResetKind) -> None:
        """
        Carry out a reset; a part that fails is logged, not raised.

        Parameters
        ----------
        kind : ResetKind
            The reset asked for.
        """


class DeviceProbe(Protocol):
    """Finds out the device's facts."""

    async def probe(self) -> DeviceFacts:
        """
        Gather the device's facts.

        Returns
        -------
        DeviceFacts
            What was found; a fact that can't be read is None.
        """


class Clock(Protocol):
    """The current time."""

    def now(self) -> datetime:
        """
        The current time.

        Returns
        -------
        datetime
            Aware datetime.
        """

    def synced(self) -> bool:
        """
        Say whether the clock has been synchronised since boot.

        Returns
        -------
        bool
            True once the time can be trusted.
        """


class KeyStore(Protocol):
    """Holds the API key."""

    def read(self) -> str | None:
        """
        Read the key.

        Returns
        -------
        str | None
            The key, or None when none is set.
        """

    def present(self) -> bool:
        """
        Say whether a key is set.

        Returns
        -------
        bool
            True when there is a key.
        """

    def write(self, *, key: str) -> None:
        """
        Store a key.

        Parameters
        ----------
        key : str
            The key.

        Raises
        ------
        ValueError
            When it is not a valid key.
        """

    def clear(self) -> None:
        """Remove the key."""
