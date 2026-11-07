"""What the device knows about itself."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final


@dataclass(frozen=True)
class Credential:
    """
    How the device's pages name the API key and refuse a bad one.

    Attributes
    ----------
    label : str
        The key's name, such as a form field's label.
    refusal : str
        Why the setup page refused a key.
    not_saved : str
        Why the Device page didn't save a key.
    """

    label: str
    refusal: str
    not_saved: str


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
        Whether an API key is set.
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


class MenuRow(StrEnum):
    """The Settings menu's rows, top to bottom."""

    WIFI = 'Wi-Fi'
    DEVICE = 'Device'
    PHONE = 'Open on your phone'
    SLEEP = 'Sleep now'
    REBUILD = 'Rebuild timetable now'
    RESTART = 'Restart'
    SHUT_DOWN = 'Shut down'
    RESET = 'Reset settings'


MENU_ROWS: Final[tuple[MenuRow, ...]] = tuple(MenuRow)


class PanelScreen(StrEnum):
    """The screens the Settings menu shows."""

    MENU = 'menu'
    DEVICE = 'device'
    PHONE = 'phone'


@dataclass(frozen=True)
class Menu:
    """
    The open Settings menu.

    Attributes
    ----------
    screen : PanelScreen
        The screen shown.
    cursor : int
        Index into `MENU_ROWS` of the chosen row.
    """

    screen: PanelScreen = PanelScreen.MENU
    cursor: int = 0


class ConfirmAction(StrEnum):
    """What a confirm on the panel does once accepted."""

    RESTART = 'restart'
    SHUT_DOWN = 'shut_down'
    RESET_SETTINGS = 'reset_settings'
    ERASE = 'erase'
    JOIN_WIFI = 'join_wifi'


class Origin(StrEnum):
    """Where a confirm was asked for."""

    PANEL = 'panel'
    PHONE = 'phone'


@dataclass(frozen=True)
class Confirm:
    """
    A question waiting for a knob press.

    Attributes
    ----------
    action : ConfirmAction
        What a bottom press does.
    origin : Origin
        Where it was asked for.
    asked_at : datetime
        When it was asked for.
    """

    action: ConfirmAction
    origin: Origin
    asked_at: datetime


class JoinOutcome(StrEnum):
    """How an attempt to join a network ended."""

    JOINED = 'joined'
    WRONG_PASSWORD = 'wrong_password'
    NOT_FOUND = 'not_found'
    FAILED = 'failed'


class WifiPhase(StrEnum):
    """Where Wi-Fi setup from the panel has got to."""

    SCANNING = 'scanning'
    HOTSPOT = 'hotspot'
    JOINING = 'joining'
    FAILED = 'failed'


@dataclass(frozen=True)
class WifiSetup:
    """
    Wi-Fi setup open on the panel.

    Attributes
    ----------
    phase : WifiPhase
        Where setup has got to.
    password : str
        The hotspot password, or empty before it starts. Kept out of
        the repr.
    networks : tuple[WifiNetwork, ...]
        The networks the scan found.
    until : datetime | None
        When the hotspot closes by itself, or None before it starts.
    ssid : str | None
        The network being joined, or None.
    error : str | None
        Why the hotspot or the last join failed, or None.
    """

    phase: WifiPhase
    password: str = field(default='', repr=False)
    networks: tuple[WifiNetwork, ...] = ()
    until: datetime | None = None
    ssid: str | None = None
    error: str | None = None


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
        API calls made today.
    key_set : bool | None
        Whether an API key is set; None until known.
    facts : DeviceFacts | None
        The latest probe, or None before the first.
    asked : ConfirmAction | None
        What a phone asked for that waits on the panel, or None.
    setup : WifiSetup | None
        Wi-Fi setup open on the panel, or None.
    """

    timetable: str | None
    schema: str | None
    rebuilding: bool
    rebuild_failed: bool
    calls_today: int
    key_set: bool | None
    facts: DeviceFacts | None
    asked: ConfirmAction | None = None
    setup: WifiSetup | None = None


class ResetKind(StrEnum):
    """What a reset clears."""

    SETTINGS = 'settings'
    ERASE = 'erase'


class PowerAction(StrEnum):
    """What a power request does; the value is the systemctl verb."""

    OFF = 'poweroff'
    RESTART = 'reboot'


POWER_ACTIONS: Final[dict[str, PowerAction]] = {
    action.value: action for action in PowerAction
}


@dataclass(frozen=True)
class Panel:
    """
    The device's own state.

    Attributes
    ----------
    key_set : bool | None
        Whether an API key is set; None until known.
    facts : DeviceFacts | None
        The latest probe, or None before the first.
    probed_at : datetime | None
        When a probe was last asked for, or None.
    resetting : ResetKind | None
        The reset under way, or None.
    menu : Menu | None
        The open Settings menu, or None.
    confirm : Confirm | None
        The question shown, or None.
    touched_at : datetime | None
        When a knob last touched the open menu, or None.
    probing : bool
        Whether a probe asked for has not answered yet.
    asleep : bool
        Whether the screen is asleep.
    wifi : WifiSetup | None
        Wi-Fi setup open, or None.
    awake_until : datetime | None
        When a wake in quiet hours ends, or None.
    by_quiet : bool
        Whether the sleep is quiet hours', which a tick can end.
    """

    key_set: bool | None = None
    facts: DeviceFacts | None = None
    probed_at: datetime | None = None
    resetting: ResetKind | None = None
    menu: Menu | None = None
    confirm: Confirm | None = None
    touched_at: datetime | None = None
    probing: bool = False
    asleep: bool = False
    wifi: WifiSetup | None = None
    awake_until: datetime | None = None
    by_quiet: bool = False
