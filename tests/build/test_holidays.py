"""Tests for finding public holidays in a GTFS bundle."""

from datetime import date
from pathlib import Path
from typing import Final

from paperroute.build.holidays import holiday_dates
from tests.helpers import write_feed

WEEKDAYS: Final[dict[str, str]] = {
    'monday': '1', 'tuesday': '1', 'wednesday': '1', 'thursday': '1',
    'friday': '1', 'saturday': '0', 'sunday': '0',
    'start_date': '20260101', 'end_date': '20260228'}
START: Final[date] = date(2026, 1, 24)
AUSTRALIA_DAY: Final[date] = date(2026, 1, 26)


def bundle(
    *,
    tmp_path: Path,
    services: int,
    removed: int,
    day: date = AUSTRALIA_DAY,
    saturday: str = '0',
) -> Path:
    """
    Write a bundle of weekday services with some removed on one day.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.
    services : int
        Number of services.
    removed : int
        How many of them are removed on the day.
    day : date
        The day services are removed on.
    saturday : str
        The services' saturday flag.

    Returns
    -------
    Path
        The zip.
    """
    ids = [f's{number}' for number in range(services)]
    return write_feed(path=tmp_path / 'bundle.zip', files={
        'calendar.txt': [
            {'service_id': service, **WEEKDAYS, 'saturday': saturday}
            for service in ids
        ],
        'calendar_dates.txt': [
            {
                'service_id': service,
                'date': day.strftime('%Y%m%d'),
                'exception_type': '2',
            }
            for service in ids[:removed]
        ],
    })


def found(*, archive: Path) -> frozenset[date]:
    """
    Find the holidays in a fortnight from START.

    Parameters
    ----------
    archive : Path
        GTFS zip.

    Returns
    -------
    frozenset[date]
        Holidays found.
    """
    return holiday_dates(archive=archive, start=START, days=14)


def test_holiday_found(tmp_path: Path) -> None:
    """Ten of twelve weekday services removed makes a holiday."""
    archive = bundle(tmp_path=tmp_path, services=12, removed=10)
    assert found(archive=archive) == {AUSTRALIA_DAY}


def test_three_quarters_removed_is_holiday(tmp_path: Path) -> None:
    """Exactly three quarters removed is enough."""
    archive = bundle(tmp_path=tmp_path, services=12, removed=9)
    assert found(archive=archive) == {AUSTRALIA_DAY}


def test_under_three_quarters_is_not(tmp_path: Path) -> None:
    """Under three quarters removed is a normal day."""
    archive = bundle(tmp_path=tmp_path, services=12, removed=8)
    assert found(archive=archive) == frozenset()


def test_too_few_services(tmp_path: Path) -> None:
    """Under ten weekday services never make a holiday."""
    archive = bundle(tmp_path=tmp_path, services=9, removed=9)
    assert found(archive=archive) == frozenset()


def test_weekend_never_holiday(tmp_path: Path) -> None:
    """Removals on a Saturday don't count."""
    archive = bundle(
        tmp_path=tmp_path, services=12, removed=12, day=date(2026, 1, 31),
    )
    assert found(archive=archive) == frozenset()


def test_mixed_week_services_ignored(tmp_path: Path) -> None:
    """Services that also run on Saturday aren't weekday services."""
    archive = bundle(
        tmp_path=tmp_path, services=12, removed=12, saturday='1',
    )
    assert found(archive=archive) == frozenset()


def test_outside_window_ignored(tmp_path: Path) -> None:
    """A holiday after the window isn't listed."""
    archive = bundle(
        tmp_path=tmp_path, services=12, removed=12, day=date(2026, 2, 16),
    )
    assert found(archive=archive) == frozenset()
