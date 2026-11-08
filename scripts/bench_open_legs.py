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

from paperroute_sydney.city import SYDNEY, SYDNEY_ZONE
from paperroute_transit.adapters.leg_cache import LegCache
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.chains import LegKey, chains_of, leg_key
from paperroute_transit.core.connection_model import (
    LEG_EXTRA,
    default_allow,
    direct_leg,
    walk_metres,
    way_legs,
)
from paperroute_transit.core.live import LiveDeparture, resolved
from paperroute_transit.core.model import Change, Pin, StopPlace, Way

DEFAULT_DB: Final[Path] = Path('data/timetable.db')
DEFAULT_REPEAT: Final[int] = 5
DEFAULT_PAIRS: Final[tuple[str, ...]] = (
    'Parramatta Station:Westmead Station',
    'Central Station:Town Hall Station',
    'Parramatta Station:Central Station',
    'Constitution Hill, North West Twy:Westmead Station',
)
WORK_SHAPED: Final[str] = '2145615:215254:2145648:214510'
WINDOW: Final[timedelta] = timedelta(hours=3)
LATER: Final[timedelta] = timedelta(minutes=1)
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


def time_cached(
    *,
    repo: SqliteTimetableRepository,
    pin: Pin,
    start: datetime,
    repeat: int,
) -> list[float]:
    """
    Time a read a minute after a first one, through a fresh cache.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    pin : Pin
        From and To, of any kind and route.
    start : datetime
        Earliest departure of the first read.
    repeat : int
        Number of runs.

    Returns
    -------
    list[float]
        Each second read's milliseconds.
    """
    end = start + WINDOW + LEG_EXTRA
    millis = []
    for _ in range(repeat):
        cache = LegCache(zone=SYDNEY_ZONE)
        cache.departures(timetable=repo, pin=pin, start=start, end=end)
        began = time.perf_counter()
        cache.departures(
            timetable=repo, pin=pin, start=start + LATER, end=end + LATER,
        )
        millis.append((time.perf_counter() - began) * 1000)
    return millis


def pair_line(
    *,
    repo: SqliteTimetableRepository,
    pair: str,
    start: datetime,
    repeat: int,
    cached: bool = False,
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
    cached : bool
        Also time a read a minute later through the leg cache.

    Returns
    -------
    str | None
        ``{from} -> {to}: {rows} rows, median {ms} ms, max {ms} ms``,
        then ``, cached median {ms} ms, max {ms} ms`` when cached; None
        when a stop is not in the timetable.
    """
    names = pair.split(':', 1)
    found = [find_stop(repo=repo, text=text) for text in names]
    if len(found) != 2 or found[0] is None or found[1] is None:
        return None
    pin = Pin(origin=found[0], destination=found[1], kind=None)
    rows, millis = time_pair(repo=repo, pin=pin, start=start, repeat=repeat)
    text = f'{found[0].name} -> {found[1].name}: {rows} rows, ' + spread(
        millis=millis,
    )
    if cached:
        text += ', cached ' + spread(
            millis=time_cached(repo=repo, pin=pin, start=start, repeat=repeat),
        )
    return text


def spread(*, millis: list[float]) -> str:
    """
    Describe a set of timings.

    Parameters
    ----------
    millis : list[float]
        Milliseconds.

    Returns
    -------
    str
        ``median {ms} ms, max {ms} ms``.
    """
    return (
        f'median {statistics.median(millis):.1f} ms, '
        f'max {max(millis):.1f} ms'
    )


def way_pin(*, stops: Sequence[StopPlace]) -> Pin:
    """
    Build a pin with one way through the given stops.

    Parameters
    ----------
    stops : Sequence[StopPlace]
        From, then an alight and a board stop per change, then To.

    Returns
    -------
    Pin
        The pin, each change allowed its default walk time.
    """
    changes = tuple(
        Change(
            alight=alight,
            board=board,
            allow=default_allow(
                metres=walk_metres(alight=alight, board=board),
            ),
        )
        for alight, board in zip(stops[1:-1:2], stops[2:-1:2])
    )
    return Pin(
        origin=stops[0],
        destination=stops[-1],
        kind=None,
        ways=(Way(changes=changes),),
    )


def loaded(
    *, repo: SqliteTimetableRepository, leg: Pin, start: datetime, index: int,
) -> tuple[LiveDeparture, ...]:
    """
    Load one leg's departures as on time, sorted by when they leave.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    leg : Pin
        Leg pin.
    start : datetime
        Earliest departure.
    index : int
        Leg number from 0; each later leg is searched `LEG_EXTRA` further.

    Returns
    -------
    tuple[LiveDeparture, ...]
        The departures.
    """
    end = start + WINDOW + LEG_EXTRA * index
    items = resolved(
        departures=repo.departures(pin=leg, start=start, end=end),
        snapshots={},
        now=start,
        limit=timedelta(),
        zone=SYDNEY_ZONE,
        plan=SYDNEY.feeds,
    )
    return tuple(sorted(items, key=lambda item: item.departs))


def time_chains(
    *,
    repo: SqliteTimetableRepository,
    pin: Pin,
    start: datetime,
    repeat: int,
) -> tuple[int, list[float]]:
    """
    Load a pin's legs once, then run the chain search repeatedly.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    pin : Pin
        Pin with one way.
    start : datetime
        Earliest departure.
    repeat : int
        Number of runs.

    Returns
    -------
    tuple[int, list[float]]
        The chains found and each run's milliseconds.
    """
    lists: dict[LegKey, tuple[LiveDeparture, ...]] = {
        leg_key(leg=leg): loaded(repo=repo, leg=leg, start=start, index=index)
        for index, leg in enumerate(way_legs(pin=pin, way=pin.ways[0]))
    }
    direct = loaded(repo=repo, leg=direct_leg(pin=pin), start=start, index=0)
    found = 0
    millis = []
    for _ in range(repeat):
        began = time.perf_counter()
        found = len(
            chains_of(pin=pin, lists=lists, direct=direct, now=start),
        )
        millis.append((time.perf_counter() - began) * 1000)
    return found, millis


def chain_line(
    *,
    repo: SqliteTimetableRepository,
    spec: str,
    start: datetime,
    repeat: int,
) -> str | None:
    """
    Time the chain search over one way and describe the result.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    spec : str
        ``from:alight:board:to``, with a second ``alight:board`` for two
        changes; each a stop ID or an exact stop name.
    start : datetime
        Earliest departure.
    repeat : int
        Number of runs.

    Returns
    -------
    str | None
        ``{from} -> {to} chains: {n} chains, median {ms} ms, max {ms}
        ms``; None when a stop is unknown or the stops are not a way.
    """
    found = [find_stop(repo=repo, text=text) for text in spec.split(':')]
    stops = [stop for stop in found if stop is not None]
    if len(stops) != len(found) or len(stops) not in (4, 6):
        return None
    pin = way_pin(stops=stops)
    count, millis = time_chains(
        repo=repo, pin=pin, start=start, repeat=repeat,
    )
    return (
        f'{stops[0].name} -> {stops[-1].name} chains: {count} chains, '
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
    result.add_argument(
        '--chains',
        nargs='*',
        metavar='FROM:ALIGHT:BOARD:TO',
        help='also time chains over these ways; a WORK-shaped way if empty',
    )
    result.add_argument(
        '--cached',
        action='store_true',
        help='also time a read a minute later through the leg cache',
    )
    return result


def main(  # pylint: disable=too-many-statements
    argv: Sequence[str] | None = None,
) -> int:
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
    at = args.at or next_weekday_start(now=datetime.now(SYDNEY_ZONE))
    start = at if at.tzinfo else at.replace(tzinfo=SYDNEY_ZONE)
    status = 0
    with closing(
        SqliteTimetableRepository(
            path=args.db,
            city=SYDNEY,
        ),
    ) as repo:
        for pair in args.pairs:
            line = pair_line(
                repo=repo,
                pair=pair,
                start=start,
                repeat=args.repeat,
                cached=args.cached,
            )
            if line is None:
                print(f'unknown stop in {pair}', file=sys.stderr)
                status = 1
            else:
                print(line)
        specs = [] if args.chains is None else args.chains or [WORK_SHAPED]
        for spec in specs:
            line = chain_line(
                repo=repo, spec=spec, start=start, repeat=args.repeat,
            )
            if line is None:
                print(f'unknown way {spec}', file=sys.stderr)
                status = 1
            else:
                print(line)
    return status


if __name__ == '__main__':
    sys.exit(main())
