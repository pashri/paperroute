"""Device warnings shown in the banner, and when feeds are not polled."""

from typing import Final

from paperroute_device.core.text import DOT
from paperroute_transit.core.state_model import State

NO_KEY: Final[str] = f'No API key{DOT}set it on your phone'
NO_WIFI: Final[str] = (
    f'No Wi-Fi{DOT}timetable times{DOT}hold both knobs, then Wi-Fi'
)


def device_banner(*, state: State) -> str | None:
    """
    Choose the device warning the banner shows, if any.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str | None
        `NO_WIFI` while offline, else `NO_KEY` when no API key is set,
        else None.
    """
    if offline(state=state):
        return NO_WIFI
    if state.panel.key_set is False:
        return NO_KEY
    return None


def feeds_held(*, state: State) -> bool:
    """
    Say whether the live feeds must not be polled.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True when the screen is asleep, offline, Wi-Fi setup is open,
        no API key is set or there is no timetable. An unknown key polls.
    """
    no_key = state.panel.key_set is False
    asleep = state.panel.asleep or offline(state=state)
    setup = state.panel.wifi is not None
    return asleep or setup or no_key or state.rebuild.missing


def offline(*, state: State) -> bool:
    """
    Say whether the device knows it has joined no Wi-Fi network.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True when the latest facts say NetworkManager manages Wi-Fi and
        no network is joined; False without facts or NetworkManager,
        and while Wi-Fi setup is open.
    """
    facts = state.panel.facts
    if facts is None or not facts.wifi.managed:
        return False
    if state.panel.wifi is not None:
        return False
    return facts.wifi.ssid is None
