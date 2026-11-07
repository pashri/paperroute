"""What the Wi-Fi setup screen says."""

from typing import Final

from paperroute_device.core.text import HINT_DOT

SETUP_SSID: Final[str] = 'paperroute-setup'
SETUP_ADDRESS: Final[str] = '10.42.0.1'
START_FAILED: Final[str] = "Couldn't start Wi-Fi setup"
SETUP_STEPS: Final[tuple[str, ...]] = (
    f'1. Scan the code with your phone. It joins the network {SETUP_SSID}.',
    f'2. Open {SETUP_ADDRESS}/setup, pick your Wi-Fi and type its password.',
    '3. paperroute connects and this screen closes.',
)
SCANNING_LINE: Final[str] = 'Looking for Wi-Fi networks...'


def no_camera_line(*, password: str) -> str:
    """
    Say how to join the hotspot without scanning the code.

    Parameters
    ----------
    password : str
        The password as shown.

    Returns
    -------
    str
        The line.
    """
    return (
        f'No camera? Join {SETUP_SSID}, password {password},'
        f' open {SETUP_ADDRESS}/setup'
    )


def setup_footer(*, minutes_left: int) -> str:
    """
    Say how long the hotspot stays up and how to cancel.

    Parameters
    ----------
    minutes_left : int
        Whole minutes until it closes.

    Returns
    -------
    str
        The footer.
    """
    return (
        f'New password each time{HINT_DOT}closes in {minutes_left} min'
        f'{HINT_DOT}Top: press to cancel'
    )


def joining_line(*, ssid: str | None) -> str:
    """
    Say which network is being joined.

    Parameters
    ----------
    ssid : str | None
        The network, or None.

    Returns
    -------
    str
        The line.
    """
    return f'Joining {ssid or "the network"}...'
