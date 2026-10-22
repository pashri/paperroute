"""Device warnings shown in the banner, and when feeds are not polled."""

from typing import Final

from paperroute.core.state_model import State
from paperroute.core.stop_view import DOT

NO_KEY: Final[str] = f'No API key{DOT}set it on your phone'


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
        `NO_KEY` when no API key is set, else None.
    """
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
        True when the screen is asleep, no API key is set or there is
        no timetable. An unknown key polls.
    """
    no_key = state.panel.key_set is False
    return state.panel.asleep or no_key or state.rebuild.missing


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
        no network is joined; False without facts or NetworkManager.
    """
    facts = state.panel.facts
    if facts is None or not facts.wifi.managed:
        return False
    return facts.wifi.ssid is None
