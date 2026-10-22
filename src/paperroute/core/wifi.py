"""Wi-Fi setup from the panel."""

from typing import Final

SETUP_SSID: Final[str] = 'paperroute-setup'
WIFI_SPECIAL: Final[str] = '\\;,:"'


def wifi_escaped(*, text: str) -> str:
    """
    Escape a field of a WIFI: code.

    Parameters
    ----------
    text : str
        A network name or password.

    Returns
    -------
    str
        The text with a backslash before each of ``\\ ; , : "``.
    """
    return ''.join(
        f'\\{char}' if char in WIFI_SPECIAL else char for char in text
    )


def wifi_qr(*, password: str) -> str:
    """
    Give the text of the code that joins the setup hotspot.

    Parameters
    ----------
    password : str
        The hotspot password.

    Returns
    -------
    str
        A WIFI: code for a WPA network, each field escaped.
    """
    ssid = wifi_escaped(text=SETUP_SSID)
    return f'WIFI:T:WPA;S:{ssid};P:{wifi_escaped(text=password)};;'
