"""Tests for the card a transit app gives the device kit."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from paperroute_device.core.device_model import AppCard
from paperroute_device.core.text import DOT
from paperroute_testkit.city_setting import city_under_test
from paperroute_transit.adapters.app_card import built_at, transit_card

ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
NOW: Final[datetime] = datetime(2026, 11, 16, 9, 0, tzinfo=ZONE)
OUT_OF_DATE: Final[str] = (
    'Sydney\'s timetable is out of date and will be rebuilt. The panel '
    'shows "Loading timetable" for up to 40 minutes.'
)
NONE_YET: Final[str] = (
    'Sydney has no timetable yet and will build one. The panel shows '
    '"Loading timetable" for up to 40 minutes.'
)


def card(
    built: datetime | None, about: str = 'Buses, trains and ferries',
) -> AppCard:
    """
    Give Sydney's card for a build time.

    Parameters
    ----------
    built : datetime | None
        When the timetable was built, or None.
    about : str
        What the app shows.

    Returns
    -------
    AppCard
        The card at `NOW`.
    """
    return transit_card(
        name='Sydney', about=about, minutes=40, built=built, now=NOW,
    )


def test_card_gives_the_age_in_days() -> None:
    """A timetable 12 days old says so and is due a rebuild."""
    given = card(NOW - timedelta(days=12))
    assert given.name == 'Sydney'
    assert given.status == (
        f'Buses, trains and ferries{DOT}timetable built 12 days ago'
    )
    assert given.wait == OUT_OF_DATE


def test_card_built_today_has_no_wait() -> None:
    """A timetable built this morning gives its time and no wait."""
    given = card(NOW.replace(hour=6, minute=30))
    assert given.status.endswith(f'{DOT}timetable built today at 6:30')
    assert given.wait == ''


def test_card_built_last_night_is_still_fresh() -> None:
    """A timetable built yesterday within `STALE_AFTER` has no wait."""
    given = card(NOW - timedelta(hours=29, minutes=30))
    assert given.status.endswith('timetable built yesterday')
    assert given.wait == ''


def test_card_two_days_old_is_out_of_date() -> None:
    """A timetable two days old is due a rebuild."""
    given = card(NOW - timedelta(days=2))
    assert given.status.endswith('timetable built 2 days ago')
    assert given.wait != ''


def test_card_without_a_timetable() -> None:
    """No timetable says so, and says one will be built."""
    given = card(None)
    assert given.status == f'Buses, trains and ferries{DOT}no timetable yet'
    assert given.wait == NONE_YET


def test_card_without_about_has_no_leading_dot() -> None:
    """An app with no description gives the timetable's line alone."""
    assert card(None, about='').status == 'no timetable yet'


def test_built_at_reads_the_files_build_time(tmp_path: Path) -> None:
    """The file's `built_at` is read as a time in the city's zone."""
    timetable = tmp_path / 'timetable.db'
    with closing(sqlite3.connect(timetable)) as conn:
        with conn:
            conn.execute('CREATE TABLE stops (stop_id TEXT PRIMARY KEY)')
            conn.execute(
                'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)',
            )
            conn.execute(
                'INSERT INTO meta VALUES (?, ?)',
                ('built_at', '2026-11-04T03:31:00+11:00'),
            )
    city = city_under_test()
    found = built_at(city=city, timetable=timetable)
    assert found == datetime.fromisoformat('2026-11-04T03:31:00+11:00')
    assert found is not None and found.tzinfo is city.zone


@pytest.mark.parametrize('content', [None, b'not a database'])
def test_built_at_is_none_without_a_readable_file(
    tmp_path: Path, content: bytes | None,
) -> None:
    """No file, or one that is not a database, gives no build time."""
    timetable = tmp_path / 'timetable.db'
    if content is not None:
        timetable.write_bytes(content)
    assert built_at(city=city_under_test(), timetable=timetable) is None


def test_built_at_is_none_when_the_read_fails(tmp_path: Path) -> None:
    """A timetable with no `meta` table gives no build time."""
    timetable = tmp_path / 'timetable.db'
    with closing(sqlite3.connect(timetable)) as conn:
        with conn:
            conn.execute('CREATE TABLE stops (stop_id TEXT PRIMARY KEY)')
    assert built_at(city=city_under_test(), timetable=timetable) is None
