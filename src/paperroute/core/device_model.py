"""What the device knows about itself."""

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class Panel:
    """
    The device's own state.

    Attributes
    ----------
    key_set : bool | None
        Whether a TfNSW API key is set; None until known.
    """

    key_set: bool | None = None


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


class JoinOutcome(StrEnum):
    """How an attempt to join a network ended."""

    JOINED = 'joined'
    WRONG_PASSWORD = 'wrong_password'
    NOT_FOUND = 'not_found'
    FAILED = 'failed'
