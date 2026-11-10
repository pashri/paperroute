"""The events the kit's reducer takes."""

from dataclasses import dataclass, field
from datetime import datetime

from paperroute_device.core.device_events import DeviceRequest, JoinAsked
from paperroute_device.core.device_model import (
    DeviceFacts,
    JoinOutcome,
    PowerAction,
    ResetKind,
    WifiNetwork,
)
from paperroute_device.core.device_settings import QuietMode, Window
from paperroute_hardware.core.knob_events import KnobEvent, KnobsHeld


@dataclass(frozen=True)
class Tick:
    """Time passed; `synced` says the clock was set since boot."""

    now: datetime
    synced: bool = True


@dataclass(frozen=True)
class QuitRequested:
    """The user asked to quit (development monitor only)."""


@dataclass(frozen=True)
class DeviceProbed:
    """
    The device's facts were gathered.

    Attributes
    ----------
    facts : DeviceFacts
        What was found.
    """

    facts: DeviceFacts


@dataclass(frozen=True)
class DeviceProbeFailed:
    """Gathering the device's facts failed."""


@dataclass(frozen=True)
class PowerFailed:
    """Shutting down or restarting didn't happen."""

    action: PowerAction


@dataclass(frozen=True)
class ResetDone:
    """A reset has finished, whether or not every part of it worked."""

    kind: ResetKind


@dataclass(frozen=True)
class HotspotStarted:
    """
    The setup hotspot is up.

    Attributes
    ----------
    networks : tuple[WifiNetwork, ...]
        The networks the scan before it found.
    password : str
        The hotspot password. Kept out of the repr.
    """

    networks: tuple[WifiNetwork, ...]
    password: str = field(repr=False)


@dataclass(frozen=True)
class HotspotFailed:
    """The setup hotspot did not start."""


@dataclass(frozen=True)
class WifiJoined:
    """The device joined a network from Wi-Fi setup."""

    ssid: str


@dataclass(frozen=True)
class WifiJoinFailed:
    """
    Joining a network from Wi-Fi setup failed.

    Attributes
    ----------
    ssid : str
        The network asked for.
    outcome : JoinOutcome
        How the join ended.
    hotspot : bool
        Whether the setup hotspot is back up.
    """

    ssid: str
    outcome: JoinOutcome
    hotspot: bool


@dataclass(frozen=True)
class WifiSetupStopped:
    """The setup hotspot was taken down."""


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class DeviceChange:
    """
    The device settings a Settings page post changes.

    Attributes
    ----------
    quiet_mode : QuietMode | None
        How quiet hours are chosen.
    my_hours : Window | None
        The quiet hours chosen by hand.
    rotate : bool | None
        Whether the panel is turned 180 degrees.
    larger_text : bool | None
        Whether list rows use larger text.
    inverted_lists : bool | None
        Whether list rows are white on black.
    clean_after : int | None
        Quick patches before a cleaning full refresh.
    swap_knobs : bool | None
        Whether the knobs swap roles.

    Notes
    -----
    A None field is left as it is.
    """

    quiet_mode: QuietMode | None = None
    my_hours: Window | None = None
    rotate: bool | None = None
    larger_text: bool | None = None
    inverted_lists: bool | None = None
    clean_after: int | None = None
    swap_knobs: bool | None = None


@dataclass(frozen=True)
class SettingsPosted:
    """
    The Settings page posted a change.

    Attributes
    ----------
    device : DeviceChange
        The device settings changed.
    app : object | None
        The app's own change, handed to the app; None with none.
    """

    device: DeviceChange
    app: object | None = None


WifiEvent = (
    HotspotStarted
    | HotspotFailed
    | JoinAsked
    | WifiJoined
    | WifiJoinFailed
    | WifiSetupStopped
)
KitEvent = (
    Tick
    | QuitRequested
    | DeviceProbed
    | DeviceProbeFailed
    | PowerFailed
    | ResetDone
    | WifiEvent
    | SettingsPosted
    | DeviceRequest
    | KnobEvent
    | KnobsHeld
)
