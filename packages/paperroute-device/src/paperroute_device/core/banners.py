"""The banner line: the device's warnings first, then the app's."""

from typing import Final

from paperroute_device.core.app import App
from paperroute_device.core.device_model import Panel
from paperroute_device.core.device_state import Banner, DeviceState
from paperroute_device.core.text import DOT

NO_WIFI_HEAD: Final[str] = 'No Wi-Fi'
NO_WIFI_TAIL: Final[str] = 'hold both knobs, then Wi-Fi'


def no_key_banner(*, name: str) -> str:
    """
    Say that a key is missing.

    Parameters
    ----------
    name : str
        The key's display name.

    Returns
    -------
    str
        The banner naming the key.
    """
    return f'No {name} key{DOT}set it on your phone'


def no_wifi_banner(*, phrase: str) -> str:
    """
    Say that no Wi-Fi network is joined.

    Parameters
    ----------
    phrase : str
        What still works offline, or empty.

    Returns
    -------
    str
        The banner, with the phrase and its dot left out when empty.
    """
    parts = (NO_WIFI_HEAD, phrase, NO_WIFI_TAIL)
    return DOT.join(part for part in parts if part)


def offline(*, panel: Panel) -> bool:
    """
    Say whether the device knows it has joined no Wi-Fi network.

    Parameters
    ----------
    panel : Panel
        The device's panel state.

    Returns
    -------
    bool
        True when the latest facts say NetworkManager manages Wi-Fi and
        no network is joined; False without facts or NetworkManager,
        and while Wi-Fi setup is open.
    """
    facts = panel.facts
    if facts is None or not facts.wifi.managed:
        return False
    if panel.wifi is not None:
        return False
    return facts.wifi.ssid is None


def device_banners(*, panel: Panel, phrase: str) -> tuple[Banner, ...]:
    """
    List the device's own warnings, first first.

    Parameters
    ----------
    panel : Panel
        The device's panel state.
    phrase : str
        What still works offline, for the No Wi-Fi line.

    Returns
    -------
    tuple[Banner, ...]
        No Wi-Fi while offline, then the no-key line naming the first
        missing key.
    """
    lines: list[Banner] = []
    if offline(panel=panel):
        lines.append(Banner(text=no_wifi_banner(phrase=phrase)))
    if panel.missing_keys:
        lines.append(Banner(text=no_key_banner(name=panel.missing_keys[0])))
    return tuple(lines)


def banner_line[S](
    *, panel: Panel, app: App[S], state: S, device: DeviceState,
) -> Banner | None:
    """
    Choose the line the banner shows.

    Parameters
    ----------
    panel : Panel
        The device's panel state.
    app : App[S]
        The app.
    state : S
        The app's state.
    device : DeviceState
        The device state.

    Returns
    -------
    Banner | None
        The first device warning, else the app's first line, else None.
    """
    lines = device_banners(panel=panel, phrase=app.offline_phrase)
    if lines:
        return lines[0]
    return next(iter(app.banners(state=state, device=device)), None)
