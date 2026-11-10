"""Device warnings shown in the banner, and when feeds are not polled."""

from datetime import date
from typing import Final

from paperroute_device.core.text import DOT
from paperroute_transit.core.rhythm import frame_time
from paperroute_transit.core.state_model import State

NO_WIFI: Final[str] = (
    f'No Wi-Fi{DOT}timetable times{DOT}hold both knobs, then Wi-Fi'
)
TIMETABLE_OUT_OF_DATE: Final[str] = (
    'Timetable out of date. Check the internet and key.'
)
WARN_DAYS_LEFT: Final[int] = 3  # days, today counted
DAY_NAMES: Final[tuple[str, ...]] = (
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
    'Sunday',
)


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
        `NO_WIFI` while offline, else the no-key banner naming the
        first missing key, else the timetable's end when it is near or
        past, else None.
    """
    if offline(state=state):
        return NO_WIFI
    if state.panel.missing_keys:
        return no_key_banner(name=state.panel.missing_keys[0])
    return timetable_banner(state=state)


def timetable_ends(*, last_day: date) -> str:
    """
    Say which day the timetable ends.

    Parameters
    ----------
    last_day : date
        The last day it covers.

    Returns
    -------
    str
        The warning, naming the weekday.
    """
    day = DAY_NAMES[last_day.weekday()]
    return f'Timetable ends {day}. Check the internet and key.'


def timetable_banner(*, state: State) -> str | None:
    """
    Warn when the timetable is running out or has run out.

    Today is the frame's date in the city's zone, so the text changes
    only at the city's midnight.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    str | None
        `TIMETABLE_OUT_OF_DATE` after the last day, the end's weekday
        with `WARN_DAYS_LEFT` days or fewer left, else None.
    """
    last_day = state.rebuild.last_day
    if last_day is None:
        return None
    today = frame_time(state=state).astimezone(state.city.zone).date()
    left = (last_day - today).days + 1
    if left <= 0:
        return TIMETABLE_OUT_OF_DATE
    if left <= WARN_DAYS_LEFT:
        return timetable_ends(last_day=last_day)
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
