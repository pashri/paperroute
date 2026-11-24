"""Time the Stop view's tick with and without the route gauge's buses."""

import argparse
import statistics
import sys
import time as clock_time
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Final

from paperroute_cambridge.city import CAMBRIDGE
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.gtfs_rt import parse_positions
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_effects import map_data
from paperroute_transit.core.city import City
from paperroute_transit.core.gauge_requests import (
    GaugeData,
    gauge_feeds,
    gauge_trip_ids,
    route_trips_effect,
)
from paperroute_transit.core.map_state import MapState
from paperroute_transit.core.messages import (
    LoadPatterns,
    LoadRouteTrips,
    PatternsLoaded,
    RouteTripsLoaded,
)
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LiveView, Saved, default_settings
from paperroute_transit.core.state_model import Board, State
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.measures import StopMeasures
from paperroute_transit.render.stop_view import draw_stop_view

CITIES: Final[dict[str, City]] = {'sydney': SYDNEY, 'cambridge': CAMBRIDGE}
DEFAULT_DBS: Final[dict[str, Path]] = {
    'sydney': Path('data') / 'timetable.db',
    'cambridge': Path('data') / 'cambridge' / 'timetable.db',
}
DEFAULT_REPEAT: Final[int] = 20
DEFAULT_LIMIT: Final[float] = 35.0  # percent over today
EVENING: Final[tuple[int, int]] = (17, 30)  # hour and minute of the start
SATURDAY: Final[int] = 5  # weekday number
WINDOW: Final[timedelta] = timedelta(hours=3)
GAUGE: Final[str] = 'with gauge'
TODAY: Final[str] = 'today'
MODES: Final[tuple[str, ...]] = ('both', GAUGE, TODAY)
P90: Final[int] = 90  # percentile
OVER_LIMIT: Final[int] = 2  # exit status


@dataclass(frozen=True)
class Stats:
    """
    A mode's timings.

    Attributes
    ----------
    median : float
        Median milliseconds of a tick.
    p90 : float
        90th percentile milliseconds of a tick.
    """

    median: float
    p90: float


def next_weekday_start(*, now: datetime) -> datetime:
    """
    Find the next 17:30 on a weekday.

    Parameters
    ----------
    now : datetime
        Current local time.

    Returns
    -------
    datetime
        Today at 17:30 when that is still ahead on a weekday, otherwise
        17:30 on the next weekday.
    """
    clock = time(hour=EVENING[0], minute=EVENING[1], tzinfo=now.tzinfo)
    day = now.date()
    while datetime.combine(day, clock) <= now or day.weekday() >= SATURDAY:
        day += timedelta(days=1)
    return datetime.combine(day, clock)


def start_time(*, at: datetime | None, city: City) -> datetime:
    """
    Give the moment to draw the Stop view at.

    Parameters
    ----------
    at : datetime | None
        The time asked for, or None.
    city : City
        The city.

    Returns
    -------
    datetime
        The time asked for, in the city's zone when it has none; with
        none asked for, the next weekday at 17:30.
    """
    if at is None:
        return next_weekday_start(now=datetime.now(city.zone))
    return at if at.tzinfo else at.replace(tzinfo=city.zone)


def stats_of(*, millis: list[float]) -> Stats:
    """
    Summarise some timings.

    Parameters
    ----------
    millis : list[float]
        Each run's milliseconds.

    Returns
    -------
    Stats
        The median and the 90th percentile.
    """
    ordered = sorted(millis)
    rank = max(0, -(-len(ordered) * P90 // 100) - 1)
    return Stats(median=statistics.median(ordered), p90=ordered[rank])


def tick(*, state: State) -> None:
    """
    Build and draw the Stop view as a tick does.

    Parameters
    ----------
    state : State
        State on the Stop view.
    """
    draw_stop_view(view=live_stop_view(state=state))


def timed_ticks(*, state: State, repeat: int) -> Stats:
    """
    Time repeated ticks after one untimed warm-up.

    Parameters
    ----------
    state : State
        State on the Stop view.
    repeat : int
        Number of timed runs.

    Returns
    -------
    Stats
        The median and p90 milliseconds.
    """
    tick(state=state)
    millis = []
    for _ in range(repeat):
        began = clock_time.perf_counter()
        tick(state=state)
        millis.append((clock_time.perf_counter() - began) * 1000)
    return stats_of(millis=millis)


def loaded_patterns(
    *, repo: SqliteTimetableRepository, state: State, trip_ids: frozenset[str],
) -> State:
    """
    Load patterns and their shapes into the state's map.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    state : State
        State.
    trip_ids : frozenset[str]
        The trips to load.

    Returns
    -------
    State
        State with the patterns and shapes held.
    """
    day = state.now.astimezone(state.city.zone).date()
    loaded = map_data(
        timetable=repo,
        effect=LoadPatterns(trip_ids=trip_ids, day=day),
        measures=StopMeasures(),
    )
    assert isinstance(loaded, PatternsLoaded)
    held = state.map
    return replace(
        state,
        map=replace(
            held,
            patterns={**held.patterns, **loaded.patterns},
            shapes={**held.shapes, **loaded.shapes},
        ),
    )


def with_route_trips(
    *, repo: SqliteTimetableRepository, state: State,
) -> State:
    """
    Load the hero route's trips into the state's gauge.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    state : State
        State with the hero's pattern held.

    Returns
    -------
    State
        State with the gauge's request and trips held, and the patterns
        the gauge needs.
    """
    request = route_trips_effect(
        state=state, stop_view=live_stop_view(state=state),
    )
    assert isinstance(request, LoadRouteTrips)
    loaded = map_data(timetable=repo, effect=request, measures=StopMeasures())
    assert isinstance(loaded, RouteTripsLoaded)
    gauge = GaugeData(request=request, trips=loaded.trips)
    state = replace(state, map=replace(state.map, gauge=gauge))
    return loaded_patterns(
        repo=repo,
        state=state,
        trip_ids=gauge_trip_ids(
            state=state, stop_view=live_stop_view(state=state),
        ),
    )


def stop_state(
    *,
    repo: SqliteTimetableRepository,
    city: City,
    pin: Pin,
    found: tuple[Departure, ...],
    at: datetime,
) -> State:
    """
    Build the state of the Stop view at a pin, with the gauge loaded.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    city : City
        The city.
    pin : Pin
        A pin with no Destination.
    found : tuple[Departure, ...]
        The departures of the next few hours, soonest first; not empty.
    at : datetime
        The moment drawn.

    Returns
    -------
    State
        State on the Stop view with the departures, patterns, shapes and
        the hero route's trips held, and no live positions.
    """
    saved = Saved(
        settings=default_settings(city=city),
        pins=(pin,),
        current=0,
        unpinned=None,
        view=LiveView.STOP,
    )
    state = initial_state(saved=saved, now=at, city=city)
    board = Board(
        departures=found,
        loaded_until=at + WINDOW,
        station=repo.station_for(stop_id=pin.origin.stop_id),
    )
    state = replace(state, board=board, map=MapState(centre=pin.origin.point))
    state = loaded_patterns(
        repo=repo,
        state=state,
        trip_ids=frozenset(item.trip_id for item in found),
    )
    return with_route_trips(repo=repo, state=state)


def decode_feed(
    *, data: bytes, state: State, repeat: int,
) -> tuple[State, Stats]:
    """
    Decode a saved positions feed, timing it, into the hero's feeds.

    Parameters
    ----------
    data : bytes
        A serialised vehicle-positions FeedMessage.
    state : State
        State on the Stop view.
    repeat : int
        Number of timed decodes.

    Returns
    -------
    tuple[State, Stats]
        State holding the decoded vehicles under each feed the gauge
        polls for the hero, and the decode's median and p90 milliseconds.
    """
    decode = partial(
        parse_positions,
        data=data,
        fetched_at=state.now,
        projection=state.city.projection,
    )
    snapshot = decode()
    millis = []
    for _ in range(repeat):
        began = clock_time.perf_counter()
        decode()
        millis.append((clock_time.perf_counter() - began) * 1000)
    feeds = gauge_feeds(state=state, stop_view=live_stop_view(state=state))
    held = {**state.map.positions, **dict.fromkeys(feeds, snapshot)}
    state = replace(state, map=replace(state.map, positions=held))
    return state, stats_of(millis=millis)


def without_buses(*, state: State) -> State:
    """
    Empty the gauge's buses: its route trips and the live positions.

    Parameters
    ----------
    state : State
        State on the Stop view.

    Returns
    -------
    State
        The same state with the gauge holding no trips and no positions.
    """
    gauge = replace(state.map.gauge, trips=(), positions={})
    return replace(
        state, map=replace(state.map, gauge=gauge, positions={}),
    )


def overhead(*, gauge: Stats, today: Stats) -> float:
    """
    Give how much slower the gauge makes a tick.

    Parameters
    ----------
    gauge : Stats
        Timings with the gauge's buses.
    today : Stats
        Timings without them.

    Returns
    -------
    float
        Percent over today's median.
    """
    return (gauge.median / today.median - 1) * 100


def report(*, name: str, stats: Stats) -> None:
    """
    Print a mode's median and p90.

    Parameters
    ----------
    name : str
        The mode.
    stats : Stats
        Its timings.
    """
    print(f'{name}: median {stats.median:.1f} ms, p90 {stats.p90:.1f} ms')


def parser() -> argparse.ArgumentParser:
    """
    Build the command-line parser.

    Returns
    -------
    argparse.ArgumentParser
        Parser for the options.
    """
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--city', choices=sorted(CITIES), required=True)
    result.add_argument('--stop', required=True, help='stop ID')
    result.add_argument('--db', type=Path, help="default: the city's data/")
    result.add_argument(
        '--at', type=datetime.fromisoformat, help='ISO time of the tick',
    )
    result.add_argument('--repeat', type=int, default=DEFAULT_REPEAT)
    result.add_argument(
        '--positions',
        type=Path,
        help='saved vehicle-positions .pb for the hero feed',
    )
    result.add_argument('--mode', choices=MODES, default='both')
    result.add_argument(
        '--limit',
        type=float,
        default=DEFAULT_LIMIT,
        help='percent the gauge may add to the median tick',
    )
    return result


def pin_and_departures(
    *, repo: SqliteTimetableRepository, stop: str, start: datetime,
) -> tuple[Pin, tuple[Departure, ...]] | None:
    """
    Build a pin at a stop and find its departures.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    stop : str
        The stop ID.
    start : datetime
        Earliest departure.

    Returns
    -------
    tuple[Pin, tuple[Departure, ...]] | None
        The pin, with the kind of its first departure, and the next
        hours' departures soonest first; None, after saying why on
        stderr, for an unknown stop or no departures.
    """
    try:
        origin = repo.stop(stop_id=stop)
    except LookupError:
        sys.stderr.write(f'unknown stop {stop}\n')
        return None
    pin = Pin(origin=origin, destination=None, kind=None)
    found = repo.departures(pin=pin, start=start, end=start + WINDOW)
    ordered = tuple(sorted(found, key=lambda item: item.departs))
    if not ordered:
        print(f'no departures from {stop} at {start}', file=sys.stderr)
        return None
    return replace(pin, kind=ordered[0].facts.kind), ordered


def main(argv: Sequence[str] | None = None) -> int:
    """
    Print the Stop view tick's median and p90 for each mode.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments; the process's own when None.

    Returns
    -------
    int
        0 when done; 1 when the timetable is missing, the stop is
        unknown or it has no departures then; 2 when both modes ran and
        the gauge adds more than `--limit` percent to the median.
    """
    args = parser().parse_args(argv)
    city = CITIES[args.city]
    db = args.db or DEFAULT_DBS[args.city]
    if not db.is_file():
        print(f'no timetable at {db}', file=sys.stderr)
        return 1
    if args.positions and not args.positions.is_file():
        print(f'no positions at {args.positions}', file=sys.stderr)
        return 1
    start = start_time(at=args.at, city=city)
    with closing(SqliteTimetableRepository(path=db, city=city)) as repo:
        loaded = pin_and_departures(repo=repo, stop=args.stop, start=start)
        if loaded is None:
            return 1
        state = stop_state(
            repo=repo, city=city, pin=loaded[0], found=loaded[1], at=start,
        )
    if args.positions:
        state, decoded = decode_feed(
            data=args.positions.read_bytes(),
            state=state,
            repeat=args.repeat,
        )
        print(f'feed decode: median {decoded.median:.1f} ms')
    results = {}
    if args.mode in {'both', GAUGE}:
        results[GAUGE] = timed_ticks(state=state, repeat=args.repeat)
    if args.mode in {'both', TODAY}:
        results[TODAY] = timed_ticks(
            state=without_buses(state=state), repeat=args.repeat,
        )
    for name, stats in results.items():
        report(name=name, stats=stats)
    if len(results) < len(MODES) - 1:
        return 0
    extra = overhead(gauge=results[GAUGE], today=results[TODAY])
    print(f'overhead: {extra:+.1f}%')
    if extra > args.limit:
        print(f'over the limit of {args.limit:g}%')
        return OVER_LIMIT
    return 0


if __name__ == '__main__':
    sys.exit(main())
