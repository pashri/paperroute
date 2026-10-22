"""What the panel's settings screens and the sleep screen show."""

from dataclasses import dataclass, field

from paperroute.core.device_model import WifiPhase
from paperroute.core.map_view import MapView


@dataclass(frozen=True)
class MenuView:
    """
    The Settings menu.

    Attributes
    ----------
    clock : str
        The time in the header.
    rows : tuple[str, ...]
        Every menu row, top to bottom.
    cursor : int
        Index into ``rows`` of the chosen row.
    first : int
        Index into ``rows`` of the top visible row.
    """

    clock: str
    rows: tuple[str, ...]
    cursor: int
    first: int


@dataclass(frozen=True)
class InfoRow:
    """
    One labelled line on the Device screen.

    Attributes
    ----------
    label : str
        What the line is about.
    value : str
        What it says.
    """

    label: str
    value: str


@dataclass(frozen=True)
class DeviceView:
    """
    The Device health summary.

    Attributes
    ----------
    clock : str
        The time in the header.
    rows : tuple[InfoRow, ...]
        The lines shown.
    """

    clock: str
    rows: tuple[InfoRow, ...]


@dataclass(frozen=True)
class PhoneView:
    """
    The Open on your phone screen.

    Attributes
    ----------
    clock : str
        The time in the header.
    url : str
        The address the QR code opens.
    addresses : tuple[str, ...]
        The device's IP addresses.
    network : str | None
        The Wi-Fi joined, or None.
    """

    clock: str
    url: str
    addresses: tuple[str, ...]
    network: str | None


@dataclass(frozen=True)
class WifiSetupView:
    """
    The Wi-Fi setup screen.

    Attributes
    ----------
    clock : str
        The time in the header.
    phase : WifiPhase
        Where setup has got to.
    qr : str
        The text of the code that joins the hotspot; it holds the password.
    password : str
        The hotspot password.
    address : str
        The address the setup page is on.
    minutes_left : int
        Whole minutes until the hotspot closes.
    ssid : str | None
        The network being joined, or None.
    error : str | None
        Why the last join failed, or None.
    """

    clock: str
    phase: WifiPhase
    qr: str = field(repr=False)
    password: str = field(repr=False)
    address: str
    minutes_left: int
    ssid: str | None
    error: str | None


@dataclass(frozen=True)
class ConfirmView:
    """
    A question answered with the knobs.

    Attributes
    ----------
    title : str
        The header.
    question : str
        The question, in large type.
    lines : tuple[str, ...]
        Explanation under the question.
    confirm : str
        The confirm button's label.
    """

    title: str
    question: str
    lines: tuple[str, ...]
    confirm: str


@dataclass(frozen=True)
class SleepView:
    """
    The sleep screen: a map under a band.

    Attributes
    ----------
    map : MapView
        The map drawn.
    band : str
        The text in the band.
    """

    map: MapView
    band: str


PanelView = MenuView | DeviceView | PhoneView | WifiSetupView | ConfirmView
