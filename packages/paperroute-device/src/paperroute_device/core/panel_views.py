"""What the panel's settings, notice and Wi-Fi screens show."""

from dataclasses import dataclass, field
from typing import Final

from paperroute_device.core.device_model import (
    Confirm,
    ConfirmAction,
    Origin,
    WifiPhase,
)

SLEEP_BAND: Final[int] = 56  # pixels the sleep band covers at the bottom
LOCAL_NAME: Final[str] = 'paperroute.local'
UNKNOWN: Final[str] = '-'
STRONG_DBM: Final[int] = -60
GOOD_DBM: Final[int] = -70
MENU_VISIBLE: Final[int] = 7
PHONE_ASKED: Final[str] = 'Asked for from a phone on your Wi-Fi.'
CONFIRM_TITLE: Final[str] = 'Settings'
CANCEL_LINE: Final[str] = 'Top: press to cancel'
TIMEOUT_LINE: Final[str] = 'Cancels by itself after 60 s'
CONFIRM_COPY: Final[dict[ConfirmAction, tuple[str, str, str]]] = {
    ConfirmAction.RESTART: (
        'Restart paperroute?',
        'paperroute starts again. Your pins and settings stay.',
        'restart',
    ),
    ConfirmAction.SHUT_DOWN: (
        'Shut down paperroute?',
        "paperroute switches off. The screen says when it's safe"
        ' to unplug.',
        'shut down',
    ),
    ConfirmAction.RESET_SETTINGS: (
        'Reset settings?',
        'Clears pins, settings, Wi-Fi and the key.'
        ' The timetable and map stay.',
        'reset',
    ),
    ConfirmAction.ERASE: (
        'Erase everything?',
        'Clears pins, settings, Wi-Fi, the key and the timetable.'
        ' The map stays.',
        'erase',
    ),
    ConfirmAction.JOIN_WIFI: (
        'Start Wi-Fi setup?',
        'paperroute leaves its Wi-Fi while you set up the new one.',
        'start',
    ),
}


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
        Explanation under the question: who asked, then what happens.
    cancel : str
        The cancel button's label.
    confirm : str
        The confirm button's label.
    """

    title: str
    question: str
    lines: tuple[str, ...]
    cancel: str
    confirm: str


@dataclass(frozen=True)
class NoticeView:
    """
    A screen showing a title and an optional line under it.

    Attributes
    ----------
    title : str
        The main line, such as the shutdown notice.
    detail : str
        A smaller line under the title, or empty.
    """

    title: str
    detail: str = ''


PanelView = MenuView | DeviceView | PhoneView | WifiSetupView | ConfirmView


def confirm_view(*, confirm: Confirm) -> ConfirmView:
    """
    Build the screen asking to confirm an action.

    Parameters
    ----------
    confirm : Confirm
        The question.

    Returns
    -------
    ConfirmView
        The title, question, the lines under it and the button labels;
        a phone's ask says so first.
    """
    question, happens, verb = CONFIRM_COPY[confirm.action]
    asked = (PHONE_ASKED,) if confirm.origin is Origin.PHONE else ()
    return ConfirmView(
        title=CONFIRM_TITLE,
        question=question,
        lines=(*asked, happens),
        cancel=CANCEL_LINE,
        confirm=f'Bottom: press to {verb}',
    )


def strength(*, dbm: int) -> str:
    """
    Name a signal level.

    Parameters
    ----------
    dbm : int
        Signal level in dBm.

    Returns
    -------
    str
        'strong', 'good' or 'weak'.
    """
    if dbm >= STRONG_DBM:
        return 'strong'
    return 'good' if dbm >= GOOD_DBM else 'weak'
