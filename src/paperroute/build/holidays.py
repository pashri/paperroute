"""Find public holidays from the services a GTFS bundle removes."""

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from paperroute.build.gtfs_files import DAY_NAMES, read_rows

MIN_WEEKDAY_SERVICES: Final[int] = 10
MIN_REMOVED_SHARE: Final[float] = 0.75
WEEKDAY_FLAGS: Final[tuple[str, ...]] = ('1',) * 5 + ('0',) * 2


def weekday_service(*, row: dict[str, str]) -> bool:
    """
    Say whether a calendar.txt row runs Monday to Friday only.

    Parameters
    ----------
    row : dict[str, str]
        A calendar.txt record.

    Returns
    -------
    bool
        True when every weekday flag is set and neither weekend flag is.
    """
    return tuple(row[name] for name in DAY_NAMES) == WEEKDAY_FLAGS


def weekday_ranges(*, archive: Path) -> dict[str, tuple[date, date]]:
    """
    List the Monday-to-Friday services and the dates they cover.

    Parameters
    ----------
    archive : Path
        GTFS zip file.

    Returns
    -------
    dict[str, tuple[date, date]]
        Service ID to its first and last date.
    """
    return {
        row['service_id']: (
            date.fromisoformat(row['start_date']),
            date.fromisoformat(row['end_date']),
        )
        for row in read_rows(archive=archive, member='calendar.txt')
        if weekday_service(row=row)
    }


def removed_counts(
    *, archive: Path, services: dict[str, tuple[date, date]]
) -> Counter[date]:
    """
    Count the removals of the given services on each date.

    Parameters
    ----------
    archive : Path
        GTFS zip file.
    services : dict[str, tuple[date, date]]
        Service ID to its first and last date.

    Returns
    -------
    Counter[date]
        Date to the number of the services removed on it, counting only
        removals inside a service's own dates.
    """
    counts: Counter[date] = Counter()
    for row in read_rows(archive=archive, member='calendar_dates.txt'):
        span = services.get(row['service_id'])
        day = date.fromisoformat(row['date'])
        removed = row['exception_type'] == '2'
        if removed and span and span[0] <= day <= span[1]:
            counts[day] += 1
    return counts


def holiday_dates(*, archive: Path, start: date, days: int) -> frozenset[date]:
    """
    Find the Monday-to-Friday public holidays in a window.

    A date is a holiday when at least MIN_WEEKDAY_SERVICES Monday-to-Friday
    services cover it and at least MIN_REMOVED_SHARE of those are removed
    on it. School services are removed outside term, so on an ordinary
    weekday a fair share of services is already removed.

    Parameters
    ----------
    archive : Path
        GTFS zip file.
    start : date
        First date of the window.
    days : int
        Window length in days.

    Returns
    -------
    frozenset[date]
        The holidays.
    """
    services = weekday_ranges(archive=archive)
    removed = removed_counts(archive=archive, services=services)
    found: set[date] = set()
    for day in (start + timedelta(days=offset) for offset in range(days)):
        covering = sum(
            1 for first, last in services.values() if first <= day <= last
        )
        if (
            day.weekday() < 5
            and covering >= MIN_WEEKDAY_SERVICES
            and removed[day] >= MIN_REMOVED_SHARE * covering
        ):
            found.add(day)
    return frozenset(found)
