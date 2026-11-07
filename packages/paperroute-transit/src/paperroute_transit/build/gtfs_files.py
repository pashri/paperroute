"""Read GTFS zip members as streams, work out service dates, insert rows."""

import csv
import io
import sqlite3
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import batched
from pathlib import Path
from typing import Final

BATCH: Final[int] = 10_000
DAY_NAMES: Final[tuple[str, ...]] = (
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
    'sunday')


@dataclass(frozen=True)
class Source:
    """One GTFS zip and the name its IDs are namespaced under."""

    name: str
    archive: Path


def read_rows(*, archive: Path, member: str) -> Iterator[dict[str, str]]:
    """
    Stream one CSV member of a GTFS zip as dictionaries.

    Parameters
    ----------
    archive : Path
        GTFS zip file.
    member : str
        Member name, for example 'stop_times.txt'.

    Yields
    ------
    dict[str, str]
        One row; nothing at all when the member is missing.
    """
    with zipfile.ZipFile(archive) as feed:
        if member not in feed.namelist():
            return
        with feed.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding='utf-8-sig')
            for row in csv.DictReader(text):
                yield {key.lstrip('\ufeff'): value
                       for key, value in row.items()}


def parse_time(value: str) -> int:
    """
    Convert a GTFS time to seconds after the service day's start.

    Times can pass 24:00:00 for trips that run after midnight.

    Parameters
    ----------
    value : str
        'H:MM:SS' or 'HH:MM:SS'.

    Returns
    -------
    int
        Seconds.

    Raises
    ------
    ValueError
        When the value isn't a GTFS time.
    """
    parts = value.strip().split(':')
    if len(parts) != 3:
        raise ValueError(f'not a GTFS time: {value!r}')
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds


def calendar_dates(*, row: dict[str, str], window: list[date]) -> set[date]:
    """
    List the dates in the window that one calendar.txt row runs on.

    Parameters
    ----------
    row : dict[str, str]
        A calendar.txt record.
    window : list[date]
        Dates of interest.

    Returns
    -------
    set[date]
        Matching dates.
    """
    first = date.fromisoformat(row['start_date'])
    last = date.fromisoformat(row['end_date'])
    return {day for day in window if first <= day <= last
            and row[DAY_NAMES[day.weekday()]] == '1'}


def active_dates(*, archive: Path, start: date,
                 days: int) -> dict[str, frozenset[date]]:
    """
    Work out which dates each service runs on within a window.

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
    dict[str, frozenset[date]]
        Service ID to the dates it runs; services with no dates are absent.
    """
    window = [start + timedelta(days=offset) for offset in range(days)]
    result: dict[str, set[date]] = {}
    for row in read_rows(archive=archive, member='calendar.txt'):
        result.setdefault(row['service_id'], set()).update(
            calendar_dates(row=row, window=window))
    for row in read_rows(archive=archive, member='calendar_dates.txt'):
        day = date.fromisoformat(row['date'])
        if day in window:
            change = result.setdefault(row['service_id'], set())
            added = row['exception_type'] == '1'
            (change.add if added else change.discard)(day)
    return {key: frozenset(value) for key, value in result.items() if value}


def insert_many(*, conn: sqlite3.Connection, sql: str,
                rows: Iterable[tuple[object, ...]]) -> None:
    """
    Insert rows in fixed-size batches.

    Parameters
    ----------
    conn : sqlite3.Connection
        Target database.
    sql : str
        INSERT statement.
    rows : Iterable[tuple[object, ...]]
        Values.
    """
    for chunk in batched(rows, BATCH):
        conn.executemany(sql, chunk)
