"""The timetable's banner line, and when feeds are not polled."""

from datetime import date
from typing import Final

from paperroute_device.core.banners import offline
from paperroute_transit.core.rhythm import frame_time
from paperroute_transit.core.state_model import State

OFFLINE_PHRASE: Final[str] = 'timetable times'
TIMETABLE_OUT_OF_DATE: Final[str] = (
    'Timetable out of date. Check the internet and key.'
)
WARN_DAYS_LEFT: Final[int] = 3  # days, today counted
DAY_NAMES: Final[tuple[str, ...]] = (
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
    'Sunday',
)


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
    asleep = state.panel.asleep or offline(panel=state.panel)
    setup = state.panel.wifi is not None
    return asleep or setup or no_key or state.rebuild.missing
