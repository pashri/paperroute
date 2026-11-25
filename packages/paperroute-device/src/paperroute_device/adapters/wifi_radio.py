"""Draw scanned Wi-Fi networks as radio buttons."""

import html
from typing import Final

from paperroute_device.core.device_model import WifiNetwork

STRONG: Final[int] = 67  # signal percent at or above which it is strong
GOOD: Final[int] = 34  # signal percent at or above which it is good
RADIO: Final[str] = """        <label>
          <input type="radio" name="network" value="{ssid}" required{disabled}>
          {label}
        </label>"""


def strength(*, signal: int) -> str:
    """
    Name a signal level.

    Parameters
    ----------
    signal : int
        Signal strength, 0 to 100.

    Returns
    -------
    str
        `strong`, `good` or `weak`.
    """
    if signal >= STRONG:
        return 'strong'
    return 'good' if signal >= GOOD else 'weak'


def network_label(*, network: WifiNetwork) -> str:
    """
    Describe a scanned network for its radio button.

    Parameters
    ----------
    network : WifiNetwork
        The network.

    Returns
    -------
    str
        The name, its strength, and whether it is open or unsupported;
        not escaped.
    """
    label = f'{network.ssid} ({strength(signal=network.signal)})'
    if not network.secured:
        label += ' (open)'
    if network.enterprise:
        label += ' (not supported)'
    return label


def network_radio(*, network: WifiNetwork) -> str:
    """
    Draw one scanned network as a radio button.

    Parameters
    ----------
    network : WifiNetwork
        The network.

    Returns
    -------
    str
        Escaped HTML; enterprise networks are disabled.
    """
    return RADIO.format(
        ssid=html.escape(network.ssid),
        disabled=' disabled' if network.enterprise else '',
        label=html.escape(network_label(network=network)),
    )
