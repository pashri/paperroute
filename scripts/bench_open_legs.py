"""Time any-route departure queries over a timetable database."""

import argparse
import statistics
import sys
import time
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.core.connection_model import LEG_EXTRA
from paperroute.core.model import SYDNEY, Pin, StopPlace

DEFAULT_DB: Final[Path] = Path('data/timetable.db')
DEFAULT_REPEAT: Final[int] = 5
DEFAULT_PAIRS: Final[tuple[str, ...]] = (
    'Parramatta Station:Westmead Station',
    'Central Station:Town Hall Station',
    'Parramatta Station:Central Station',
    'Constitution Hill, North West Twy:Westmead Station',
)
WINDOW: Final[timedelta] = timedelta(hours=3)
MORNING: Final[int] = 8  # hour of the default start
SATURDAY: Final[int] = 5  # weekday number


def next_weekday_start(*, now: datetime) -> datetime:
    """
    Find the next 8:00 on a weekday.

    Parameters
    ----------
    now : datetime
        Current Sydney time.

    Returns
    -------
    datetime
        Today at 8:00 when that is still ahead on a weekday, otherwise
        8:00 on the next weekday.
    """
    day = now.replace(hour=MORNING, minute=0, second=0, microsecond=0)
    while day <= now or day.weekday() >= SATURDAY:
        day += timedelta(days=1)
    return day


def find_stop(
    *, repo: SqliteTimetableRepository, text: str,
) -> StopPlace | None:
    """
    Look a stop up by its ID or its exact name.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    text : str
        A stop ID, or a name; a name picks the station-level stop first.

    Returns
    -------
    StopPlace | None
        The stop, or None when neither matches.
    """
    rows = repo.fetch(
        sql=(
            'SELECT stop_id FROM stops WHERE stop_id = :text OR name = :text '
            'ORDER BY stop_id = :text DESC, '
            "COALESCE(parent_station, '') != '', stop_id LIMIT 1"
        ),
        params={'text': text},
    )
    if not rows:
        return None
    return repo.stop(stop_id=str(rows[0][0]))


def time_pair(
    *,
    repo: SqliteTimetableRepository,
    pin: Pin,
    start: datetime,
    repeat: int,
) -> tuple[int, list[float]]:
    """
    Run one any-route query repeatedly.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    pin : Pin
        From and To, of any kind and route.
    start : datetime
        Earliest departure.
    repeat : int
        Number of runs.

    Returns
    -------
    tuple[int, list[float]]
        The rows the query returns and each run's milliseconds.
    """
    end = start + WINDOW + LEG_EXTRA
    rows = 0
    millis = []
    for _ in range(repeat):
        began = time.perf_counter()
        rows = len(repo.departures(pin=pin, start=start, end=end))
        millis.append((time.perf_counter() - began) * 1000)
    return rows, millis


def pair_line(
    *,
    repo: SqliteTimetableRepository,
    pair: str,
    start: datetime,
    repeat: int,
) -> str | None:
    """
    Time one pair of stops and describe the result.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    pair : str
        ``from:to``, each a stop ID or an exact stop name.
    start : datetime
        Earliest departure.
    repeat : int
        Number of runs.

    Returns
    -------
    str | None
        ``{from} -> {to}: {rows} rows, median {ms} ms, max {ms} ms``;
        None when a stop is not in the timetable.
    """
    names = pair.split(':', 1)
    found = [find_stop(repo=repo, text=text) for text in names]
    if len(found) != 2 or found[0] is None or found[1] is None:
        return None
    pin = Pin(origin=found[0], destination=found[1], kind=None)
    rows, millis = time_pair(repo=repo, pin=pin, start=start, repeat=repeat)
    return (
        f'{found[0].name} -> {found[1].name}: {rows} rows, '
        f'median {statistics.median(millis):.1f} ms, '
        f'max {max(millis):.1f} ms'
    )


def parser() -> argparse.ArgumentParser:
    """
    Build the command-line parser.

    Returns
    -------
    argparse.ArgumentParser
        Parser for the options.
    """
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--db', type=Path, default=DEFAULT_DB)
    result.add_argument(
        '--at', type=datetime.fromisoformat, help='ISO start time',
    )
    result.add_argument('--repeat', type=int, default=DEFAULT_REPEAT)
    result.add_argument(
        '--pairs',
        nargs='+',
        default=list(DEFAULT_PAIRS),
        metavar='FROM:TO',
        help='stop IDs or exact names',
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """
    Print the timing of each stop pair's any-route query.

    The timetable is opened read-only.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments; the process's own when None.

    Returns
    -------
    int
        0 when every pair was timed; 1 when the database is missing or a
        stop is unknown.
    """
    args = parser().parse_args(argv)
    if not args.db.is_file():
        print(f'no timetable at {args.db}', file=sys.stderr)
        return 1
    at = args.at or next_weekday_start(now=datetime.now(SYDNEY))
    start = at if at.tzinfo else at.replace(tzinfo=SYDNEY)
    status = 0
    with closing(SqliteTimetableRepository(path=args.db)) as repo:
        for pair in args.pairs:
            line = pair_line(
                repo=repo, pair=pair, start=start, repeat=args.repeat,
            )
            if line is None:
                print(f'unknown stop in {pair}', file=sys.stderr)
                status = 1
            else:
                print(line)
    return status


if __name__ == '__main__':
    sys.exit(main())
