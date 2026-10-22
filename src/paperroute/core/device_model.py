"""What the device knows about itself."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class WifiNetwork:
    """
    A Wi-Fi network seen in a scan.

    Attributes
    ----------
    ssid : str
        Network name.
    signal : int
        Signal strength, 0 to 100.
    secured : bool
        Whether it needs a password.
    enterprise : bool
        Whether it needs 802.1X sign-in, which the device can't join.
    """

    ssid: str
    signal: int
    secured: bool
    enterprise: bool


@dataclass(frozen=True)
class WifiStatus:
    """
    The Wi-Fi link as NetworkManager reports it.

    Attributes
    ----------
    managed : bool
        Whether NetworkManager could be asked at all.
    ssid : str | None
        The network joined, or None.
    dbm : int | None
        Signal level in dBm, or None.
    ip : str | None
        The IPv4 address, or None.
    """

    managed: bool = False
    ssid: str | None = None
    dbm: int | None = None
    ip: str | None = None


@dataclass(frozen=True)
class JoinRequest:
    """
    A network to join, as asked for from the phone.

    Attributes
    ----------
    ssid : str
        Network name.
    password : str
        Password; empty for an open network. Kept out of the repr.
    hidden : bool
        Whether the network hides its name.
    """

    ssid: str
    password: str = field(default='', repr=False)
    hidden: bool = False


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class DeviceFacts:
    """
    What the device found out about itself in one probe.

    Attributes
    ----------
    wifi : WifiStatus
        The Wi-Fi link.
    clock_synced : bool | None
        Whether the clock is set from the network; None when unknown.
    uptime_s : float | None
        Seconds since boot.
    cpu_c : float | None
        CPU temperature in degrees Celsius.
    disk_free : int | None
        Bytes free on the data disk.
    mem_free : int | None
        Bytes of memory available.
    version : str
        The release installed, or 'dev'.
    last_error : str | None
        The latest error logged, with its time.
    tiles_built : str | None
        When the map tiles were built, or None with no tiles.
    key_set : bool
        Whether a TfNSW API key is set.
    """

    wifi: WifiStatus
    clock_synced: bool | None
    uptime_s: float | None
    cpu_c: float | None
    disk_free: int | None
    mem_free: int | None
    version: str
    last_error: str | None
    tiles_built: str | None
    key_set: bool


@dataclass(frozen=True)
class DeviceStatus:
    """
    What the device reports about itself to observers.

    Attributes
    ----------
    timetable : str | None
        The timetable version in use.
    schema : str | None
        The timetable's schema version.
    rebuilding : bool
        Whether a timetable rebuild runs.
    rebuild_failed : bool
        Whether the last timetable rebuild failed.
    calls_today : int
        API calls made this Sydney day.
    key_set : bool | None
        Whether a TfNSW API key is set; None until known.
    facts : DeviceFacts | None
        The latest probe, or None before the first.
    """

    timetable: str | None
    schema: str | None
    rebuilding: bool
    rebuild_failed: bool
    calls_today: int
    key_set: bool | None
    facts: DeviceFacts | None


@dataclass(frozen=True)
class Panel:
    """
    The device's own state.

    Attributes
    ----------
    key_set : bool | None
        Whether a TfNSW API key is set; None until known.
    facts : DeviceFacts | None
        The latest probe, or None before the first.
    probed_at : datetime | None
        When a probe was last asked for, or None.
    """

    key_set: bool | None = None
    facts: DeviceFacts | None = None
    probed_at: datetime | None = None


class JoinOutcome(StrEnum):
    """How an attempt to join a network ended."""

    JOINED = 'joined'
    WRONG_PASSWORD = 'wrong_password'
    NOT_FOUND = 'not_found'
    FAILED = 'failed'
