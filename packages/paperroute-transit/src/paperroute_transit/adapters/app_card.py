"""The card a transit app gives the device kit: its name and timetable."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from paperroute_device.core.device_model import AppCard
from paperroute_device.core.text import DOT, clock_text, local_time
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.city import City

STALE_AFTER: Final[timedelta] = timedelta(hours=36)
LOADING: Final[str] = (
    'The panel shows "Loading timetable" for up to {minutes} minutes.'
)


def built_at(*, city: City, timetable: Path) -> datetime | None:
    """
    Read when the timetable file was built.

    Parameters
    ----------
    city : City
        The city, whose zone the time is given in.
    timetable : Path
        The timetable file.

    Returns
    -------
    datetime | None
        The build time in the city's zone; None for a missing or
        unreadable file, or one that records no build time.
    """
    with closing(
        SqliteTimetableRepository(city=city, path=timetable),
    ) as repository:
        try:
            stamp = repository.version()
        except sqlite3.Error:
            return None
    return local_time(stamp=stamp, zone=city.zone)


def built_text(*, built: datetime | None, now: datetime) -> str:
    """
    Say when the timetable was built, in whole local days.

    Parameters
    ----------
    built : datetime | None
        When the timetable was built, or None.
    now : datetime
        The time, in the local zone.

    Returns
    -------
    str
        For example 'timetable built yesterday'.
    """
    if built is None:
        return 'no timetable yet'
    zone = now.tzinfo or UTC
    days = (now.astimezone(zone).date() - built.astimezone(zone).date()).days
    if days < 1:
        at = clock_text(moment=built, zone=zone)
        return f'timetable built today at {at}'
    if days == 1:
        return 'timetable built yesterday'
    return f'timetable built {days} days ago'


def wait_text(
    *, name: str, minutes: int, built: datetime | None, now: datetime,
) -> str:
    """
    Say what making the app ready will take.

    Parameters
    ----------
    name : str
        The app's display name.
    minutes : int
        The most a timetable build takes.
    built : datetime | None
        When the timetable was built, or None.
    now : datetime
        The time.

    Returns
    -------
    str
        Empty when the timetable was built within `STALE_AFTER`.
    """
    loading = LOADING.format(minutes=minutes)
    if built is None:
        return f'{name} has no timetable yet and will build one. {loading}'
    if now - built <= STALE_AFTER:
        return ''
    return f"{name}'s timetable is out of date and will be rebuilt. {loading}"


def transit_card(
    *,
    name: str,
    about: str,
    minutes: int,
    built: datetime | None,
    now: datetime,
) -> AppCard:
    """
    Make a transit app's card from its timetable's build time.

    Parameters
    ----------
    name : str
        The app's display name.
    about : str
        What the app shows, or empty.
    minutes : int
        The most a timetable build takes.
    built : datetime | None
        When the timetable was built, or None.
    now : datetime
        The time.

    Returns
    -------
    AppCard
        The name, the status line and the wait.
    """
    status = DOT.join(
        part for part in (about, built_text(built=built, now=now)) if part
    )
    return AppCard(
        name=name,
        status=status,
        wait=wait_text(name=name, minutes=minutes, built=built, now=now),
    )
