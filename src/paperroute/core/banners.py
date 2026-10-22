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
        True when the screen is asleep or no API key is set. An unknown
        key polls.
    """
    return state.panel.asleep or state.panel.key_set is False
