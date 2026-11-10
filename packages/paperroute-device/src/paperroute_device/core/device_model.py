"""What the device knows about itself."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from paperroute_device.ports import KeyStore


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
class DeviceKey:
    """
    One API key the device keeps.

    Attributes
    ----------
    name : str
        The key's name in forms.
    credential : Credential
        How the pages name the key and refuse a bad one.
    store : KeyStore
        Where the key is kept.
    display_name : str
        The key's short name, as the banner says it.
    last_used : Callable[[], float | None]
        Wall-clock time the key last worked, or None when it hasn't.
    """

    name: str
    credential: Credential
    store: KeyStore
    display_name: str
    last_used: Callable[[], float | None] = lambda: None


def missing_keys(*, keys: Sequence[DeviceKey]) -> tuple[str, ...]:
    """
    Name the keys that are not set.

    Parameters
    ----------
    keys : Sequence[DeviceKey]
        The keys, in declaration order.

    Returns
    -------
    tuple[str, ...]
        The display name of each key whose store holds none, in order.
    """
    return tuple(key.display_name for key in keys if not key.store.present())


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
    app : object
        What the app's own probe found, or None with no app probe.
    missing_keys : tuple[str, ...]
        The display names of the keys not set, in declaration order.
    """

    wifi: WifiStatus
    clock_synced: bool | None
    uptime_s: float | None
    cpu_c: float | None
    disk_free: int | None
    mem_free: int | None
    version: str
    last_error: str | None
    missing_keys: tuple[str, ...]
    app: object = None


class MenuRow(StrEnum):
    """The kit's own rows on the Settings menu."""

    WIFI = 'Wi-Fi'
    DEVICE = 'Device'
    PHONE = 'Open on your phone'
    SLEEP = 'Sleep now'
    RESTART = 'Restart'
    SHUT_DOWN = 'Shut down'
    RESET = 'Reset settings'




@dataclass(frozen=True)
class AppRow:
    """
    A row the app adds to the Settings menu.

    Attributes
    ----------
    label : str
        The row's text.
    """

    label: str


KIT_HEAD: Final[tuple[MenuRow, ...]] = (
    MenuRow.WIFI,
    MenuRow.DEVICE,
    MenuRow.PHONE,
    MenuRow.SLEEP,
)
KIT_TAIL: Final[tuple[MenuRow, ...]] = (
    MenuRow.RESTART,
    MenuRow.SHUT_DOWN,
    MenuRow.RESET,
)


def menu_rows(*, app_rows: Sequence[AppRow]) -> tuple[MenuRow | AppRow, ...]:
    """
    List the Settings menu's rows, top to bottom.

    Parameters
    ----------
    app_rows : Sequence[AppRow]
        The app's rows.

    Returns
    -------
    tuple[MenuRow | AppRow, ...]
        The kit's first rows, the app's rows, then Restart, Shut down and
        Reset settings.
    """
    return (*KIT_HEAD, *app_rows, *KIT_TAIL)


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
        Index into the menu's rows of the chosen row.
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


@dataclass(frozen=True, kw_only=True)
class DeviceStatus:
    """
    What the device reports about itself to observers.

    Attributes
    ----------
    missing_keys : tuple[str, ...] | None
        The display names of the keys not set; None until known.
    facts : DeviceFacts | None
        The latest probe, or None before the first.
    asked : ConfirmAction | None
        What a phone asked for that waits on the panel, or None.
    setup : WifiSetup | None
        Wi-Fi setup open on the panel, or None.
    """

    missing_keys: tuple[str, ...] | None
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
    missing_keys : tuple[str, ...] | None
        The display names of the keys not set, in declaration order;
        None until known.
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

    missing_keys: tuple[str, ...] | None = None
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

    @property
    def key_set(self) -> bool | None:
        """
        Say whether every key is set.

        Returns
        -------
        bool | None
            None until known, else True when no key is missing.
        """
        return None if self.missing_keys is None else not self.missing_keys
