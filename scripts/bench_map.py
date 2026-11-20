"""Time each stage of the map at a stop."""

import argparse
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from paperroute_cambridge.city import CAMBRIDGE
from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_effects import map_data
from paperroute_transit.app_lines import loaded_day_lines
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.core.city import City
from paperroute_transit.core.geo import Point, nearest_along
from paperroute_transit.core.map_fit import (
    FrameKey,
    approach_points,
    frame_key,
    line_frame,
    line_framed,
    resting_zoom,
    steady_frame,
)
from paperroute_transit.core.map_lines import (
    LineChoice,
    plain_lines,
    plain_map_view,
)
from paperroute_transit.core.map_view import MapInputs, MapView, Marker
from paperroute_transit.core.messages import (
    DayLinesLoaded,
    LoadDayLines,
    LoadPatterns,
    LoadShapes,
    PatternsLoaded,
    ShapesLoaded,
)
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.partings import found_partings
from paperroute_transit.core.saved import BoardSettings, MapFraming
from paperroute_transit.core.stop_view import StopView
from paperroute_transit.core.strip_model import EMPTY_STOP_VIEW
from paperroute_transit.core.tiles import TileVariant
from paperroute_transit.measures import StopMeasures
from paperroute_transit.render.map_base import draw_map_base

CITIES: Final[dict[str, City]] = {'sydney': SYDNEY, 'cambridge': CAMBRIDGE}
DATA: Final[Path] = Path('data')
DEFAULT_PATHS: Final[dict[str, tuple[Path, Path]]] = {
    'sydney': (DATA / 'timetable.db', DATA / 'tiles.db'),
    'cambridge': (
        DATA / 'cambridge' / 'timetable.db',
        DATA / 'cambridge' / 'tiles.db',
    ),
}
DEFAULT_REPEAT: Final[int] = 5
EVENING: Final[tuple[int, int]] = (17, 30)  # hour and minute of the start
SATURDAY: Final[int] = 5  # weekday number
WINDOW: Final[timedelta] = timedelta(hours=3)
HEAD: Final[int] = 5  # departures drawn
TICK_BUILDS: Final[int] = 3
STALE_AFTER: Final[timedelta] = timedelta(minutes=2)
BUDGETS: Final[dict[str, int]] = {
    'day lines': 150,
    'shapes and patterns': 250,
    'tick': 15,
    'framing': 15,
    'view cold': 40,
    'view': 15,
    'base': 150,
    'frame': 25,
}  # milliseconds
BUSY_DAY_LINES: Final[int] = 300  # milliseconds
BUSY_LINES: Final[int] = 30  # lines a stop has before it is busy
OVER_BUDGET: Final[int] = 2  # exit status


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
    day = now.replace(
        hour=EVENING[0], minute=EVENING[1], second=0, microsecond=0,
    )
    while day <= now or day.weekday() >= SATURDAY:
        day += timedelta(days=1)
    return day


def timed(*, action: Callable[[], object], repeat: int) -> list[float]:
    """
    Run an action repeatedly.

    Parameters
    ----------
    action : Callable[[], object]
        What to run.
    repeat : int
        Number of runs.

    Returns
    -------
    list[float]
        Each run's milliseconds.
    """
    millis = []
    for _ in range(repeat):
        began = time.perf_counter()
        action()
        millis.append((time.perf_counter() - began) * 1000)
    return millis


def day_budget(*, lines: int) -> int:
    """
    Give the day lines' budget at a stop.

    Parameters
    ----------
    lines : int
        The lines the stop has today.

    Returns
    -------
    int
        Milliseconds; more at a stop with more than `BUSY_LINES` lines.
    """
    return BUSY_DAY_LINES if lines > BUSY_LINES else BUDGETS['day lines']


@dataclass
class Timing:
    """
    Prints each stage against its budget and keeps the ones over it.

    Attributes
    ----------
    repeat : int
        Number of runs of each stage.
    scale : float
        Multiplies every budget.
    over : list[str]
        The stages whose median was over, in the order they ran.
    """

    repeat: int
    scale: float = 1.0
    over: list[str] = field(default_factory=list)

    def report(
        self, *, name: str, millis: list[float], budget: int | None = None,
    ) -> None:
        """
        Print a stage's median and maximum beside its budget.

        Parameters
        ----------
        name : str
            The stage.
        millis : list[float]
            Each run's milliseconds.
        budget : int | None
            Milliseconds before scaling; the stage's own when None.
        """
        limit = (BUDGETS[name] if budget is None else budget) * self.scale
        median = statistics.median(millis)
        print(
            f'{name}: {median:.1f} ms, {max(millis):.1f} ms '
            f'(budget {limit:g} ms)',
        )
        if median > limit:
            self.over.append(name)


def stop_view_of(*, pin: Pin, drawn: tuple[Departure, ...]) -> StopView:
    """
    Build the Stop view a map is made beside.

    Parameters
    ----------
    pin : Pin
        The pin.
    drawn : tuple[Departure, ...]
        The departures the list heads with.

    Returns
    -------
    StopView
        A view with no rows and the departures as its head.
    """
    return replace(
        EMPTY_STOP_VIEW, origin=pin.origin.name, head_departures=drawn,
    )


def shapes_and_patterns(
    *,
    repo: SqliteTimetableRepository,
    departures: Sequence[Departure],
    day: datetime,
) -> tuple[ShapesLoaded, PatternsLoaded]:
    """
    Load the shapes and patterns of some departures.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    departures : Sequence[Departure]
        The departures drawn and the day's other lines.
    day : datetime
        The moment whose date patterns are read for.

    Returns
    -------
    tuple[ShapesLoaded, PatternsLoaded]
        The shapes, and the patterns with the rail ones on the track.
        Each call uses a fresh `StopMeasures`, so the time is a pin's
        first load.
    """
    shapes = map_data(
        timetable=repo,
        effect=LoadShapes(
            shape_ids=frozenset(
                found.shape_id for found in departures if found.shape_id
            ),
        ),
        measures=StopMeasures(),
    )
    patterns = map_data(
        timetable=repo,
        effect=LoadPatterns(
            trip_ids=frozenset(found.trip_id for found in departures),
            day=day.date(),
        ),
        measures=StopMeasures(),
    )
    assert isinstance(shapes, ShapesLoaded)
    assert isinstance(patterns, PatternsLoaded)
    return shapes, patterns


def framed(
    *,
    inputs: MapInputs,
    kind: str | None,
    city: City,
    choice: LineChoice,
    held: tuple[Point, int] | None = None,
    key: FrameKey | None = None,
) -> MapInputs:
    """
    Frame a plain pin's inputs as a tick does.

    Parameters
    ----------
    inputs : MapInputs
        Inputs centred on the From.
    kind : str | None
        The pin's kind.
    city : City
        The city.
    choice : LineChoice
        The day's other lines.
    held : tuple[Point, int] | None
        The centre and zoom drawn now, or None.
    key : FrameKey | None
        What the held frame was fitted for.

    Returns
    -------
    MapInputs
        `line_frame` for a kind chosen by line, else `steady_frame`.
    """
    if line_framed(kind=kind, modes=city.modes):
        return line_frame(inputs=inputs)
    return steady_frame(
        inputs=inputs,
        framing=MapFraming.APPROACH,
        held=held,
        key=key,
        choice=choice,
    )


def cold_view(*, inputs: MapInputs, choice: LineChoice) -> MapView:
    """
    Build a plain map's view with the placement and parting caches empty.

    Parameters
    ----------
    inputs : MapInputs
        The framed inputs.
    choice : LineChoice
        The day's other lines.

    Returns
    -------
    MapView
        The view.
    """
    nearest_along.cache_clear()
    found_partings.cache_clear()
    return plain_map_view(inputs=inputs, choice=choice)


def moved(*, view: MapView, step: int) -> MapView:
    """
    Move a vehicle marker over the map.

    Parameters
    ----------
    view : MapView
        The view.
    step : int
        Which move this is.

    Returns
    -------
    MapView
        The view with one pinned vehicle at a new place.
    """
    marker = Marker(
        route='bench',
        x=200.0 + step,
        y=200.0 + step,
        bearing=0.0,
        live=True,
        pinned=True,
    )
    return replace(view, markers=(marker,))


def first_departures(
    *, repo: SqliteTimetableRepository, pin: Pin, at: datetime,
) -> tuple[Departure, ...]:
    """
    Find the first departures a pin draws.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    pin : Pin
        A pin with no Destination.
    at : datetime
        Earliest departure.

    Returns
    -------
    tuple[Departure, ...]
        The first few departures in the next few hours, soonest first.
    """
    found = repo.departures(pin=pin, start=at, end=at + WINDOW)
    return tuple(sorted(found, key=lambda item: item.departs))[:HEAD]


def loading_stages(
    *,
    repo: SqliteTimetableRepository,
    city: City,
    pin: Pin,
    drawn: tuple[Departure, ...],
    at: datetime,
    timing: Timing,
) -> tuple[LineChoice, MapInputs]:
    """
    Time and print the stages that read the timetable.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    city : City
        The city.
    pin : Pin
        A pin with no Destination.
    drawn : tuple[Departure, ...]
        The first departures, which the map draws solid; not empty.
    at : datetime
        The moment the map is drawn at.
    timing : Timing
        Prints each stage against its budget.

    Returns
    -------
    tuple[LineChoice, MapInputs]
        The day's other lines, and the inputs with the patterns and
        shapes loaded, centred on the From.
    """
    request = LoadDayLines(
        pin=pin,
        day=at.date(),
        timetable=repo.version(),
        skip_school=BoardSettings().skip_school_buses,
    )
    days: list[DayLinesLoaded] = []
    millis = timed(
        action=lambda: days.append(
            loaded_day_lines(timetable=repo, effect=request),
        ),
        repeat=timing.repeat,
    )
    extras = days[-1].departures
    timing.report(
        name='day lines', millis=millis, budget=day_budget(lines=len(extras)),
    )
    loads: list[tuple[ShapesLoaded, PatternsLoaded]] = []
    timing.report(
        name='shapes and patterns',
        millis=timed(
            action=lambda: loads.append(
                shapes_and_patterns(
                    repo=repo, departures=(*drawn, *extras), day=at,
                ),
            ),
            repeat=timing.repeat,
        ),
    )
    shapes, patterns = loads[-1]
    choice = LineChoice(
        station=repo.station_for(stop_id=pin.origin.stop_id), extras=extras,
    )
    inputs = MapInputs(
        stop_view=stop_view_of(pin=pin, drawn=drawn),
        departures=drawn,
        centre=pin.origin.point,
        zoom=resting_zoom(step=0),
        positions={},
        patterns=patterns.patterns,
        shapes={**shapes.shapes, **patterns.shapes},
        now=at,
        zone=city.zone,
        projection=city.projection,
        stale_after=STALE_AFTER,
        ends=(pin.origin.point, None),
    )
    return choice, inputs


def drawing_stages(
    *,
    tiles: SqliteTileRepository,
    city: City,
    choice: LineChoice,
    inputs: MapInputs,
    timing: Timing,
) -> tuple[int, int]:
    """
    Time and print the stages that build and draw the map.

    Parameters
    ----------
    tiles : SqliteTileRepository
        Tiles.
    city : City
        The city.
    choice : LineChoice
        The day's other lines.
    inputs : MapInputs
        Inputs with the patterns and shapes loaded.
    timing : Timing
        Prints each stage against its budget.

    Returns
    -------
    tuple[int, int]
        The lines drawn and the rings drawn.
    """
    kind = inputs.departures[0].facts.kind
    bare = replace(inputs, patterns={})
    timing.report(
        name='tick',
        millis=timed(
            action=lambda: [
                plain_lines(inputs=bare, choice=choice)
                for _ in range(TICK_BUILDS)
            ],
            repeat=timing.repeat,
        ),
    )
    fit = framed(inputs=inputs, kind=kind, city=city, choice=choice)
    held = None if fit.centre is None else (fit.centre, fit.zoom)
    key = frame_key(
        inputs=inputs,
        framing=MapFraming.APPROACH,
        points=approach_points(inputs=inputs),
        choice=choice,
    )
    timing.report(
        name='framing',
        millis=timed(
            action=lambda: framed(
                inputs=inputs,
                kind=kind,
                city=city,
                choice=choice,
                held=held,
                key=key,
            ),
            repeat=timing.repeat,
        ),
    )
    views: list[MapView] = []
    timing.report(
        name='view cold',
        millis=timed(
            action=lambda: views.append(
                cold_view(inputs=fit, choice=choice),
            ),
            repeat=timing.repeat,
        ),
    )
    timing.report(
        name='view',
        millis=timed(
            action=lambda: views.append(
                plain_map_view(inputs=fit, choice=choice),
            ),
            repeat=timing.repeat,
        ),
    )
    view = views[-1]
    timing.report(
        name='base',
        millis=timed(
            action=lambda: draw_map_base(
                base=view.base, tiles=tiles, variant=TileVariant.GREY,
            ),
            repeat=timing.repeat,
        ),
    )
    renderer = MapRenderer(tiles=tiles)
    renderer.frame(view=view)
    moves = iter(range(timing.repeat))
    timing.report(
        name='frame',
        millis=timed(
            action=lambda: renderer.frame(
                view=moved(view=view, step=next(moves)),
            ),
            repeat=timing.repeat,
        ),
    )
    rings = len(view.base.stops) + len(view.base.dashed_stops)
    return len(view.base.lines), rings


def missing_file(*, db: Path, tiles: Path) -> str | None:
    """
    Name the first of the two files that is missing.

    Parameters
    ----------
    db : Path
        The timetable.
    tiles : Path
        The tiles file.

    Returns
    -------
    str | None
        A message naming it; None when both exist.
    """
    for path, label in ((db, 'timetable'), (tiles, 'tiles')):
        if not path.is_file():
            return f'no {label} at {path}'
    return None


def start_time(*, at: datetime | None, city: City) -> datetime:
    """
    Give the moment to draw the map at.

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


def plain_pin(*, repo: SqliteTimetableRepository, stop: str) -> Pin | None:
    """
    Build a pin at a stop, with no Destination.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    stop : str
        The stop ID.

    Returns
    -------
    Pin | None
        The pin; None, after saying so on stderr, for an unknown stop.
    """
    try:
        origin = repo.stop(stop_id=stop)
    except LookupError:
        print(f'unknown stop {stop}', file=sys.stderr)
        return None
    return Pin(origin=origin, destination=None, kind=None)


def no_departures(
    *,
    repo: SqliteTimetableRepository,
    stop: str,
    start: datetime,
    asked: bool,
) -> str:
    """
    Explain why a stop has no departures at the start.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    stop : str
        The stop ID.
    start : datetime
        The moment looked at.
    asked : bool
        Whether `--at` gave the moment.

    Returns
    -------
    str
        The message; with no `--at` it names the default start and says
        to pass one inside the timetable's dates.
    """
    message = f'no departures from {stop} at {start}'
    if asked:
        return message
    last = repo.last_day()
    span = f' (the timetable runs to {last})' if last else ''
    return (
        f'{message}; with no --at the start is the next weekday at '
        f'{EVENING[0]}:{EVENING[1]:02d}, so pass --at inside the '
        f"timetable's dates{span}"
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
    result.add_argument('--city', choices=sorted(CITIES), required=True)
    result.add_argument('--stop', required=True, help='stop ID')
    result.add_argument('--db', type=Path, help="default: the city's data/")
    result.add_argument('--tiles', type=Path, help="default: the city's data/")
    result.add_argument(
        '--at', type=datetime.fromisoformat, help='ISO time of the map',
    )
    result.add_argument('--repeat', type=int, default=DEFAULT_REPEAT)
    result.add_argument(
        '--budget-scale',
        type=float,
        default=1.0,
        help='multiplies every budget',
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """
    Print the timing of each stage of the map at a stop.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments; the process's own when None.

    Returns
    -------
    int
        0 when every stage's median is inside its budget; 1 when a file
        is missing, the stop is unknown or it has no departures then; 2
        when a median is over its budget.
    """
    args = parser().parse_args(argv)
    city = CITIES[args.city]
    db = args.db or DEFAULT_PATHS[args.city][0]
    tiles_path = args.tiles or DEFAULT_PATHS[args.city][1]
    problem = missing_file(db=db, tiles=tiles_path)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    start = start_time(at=args.at, city=city)
    timing = Timing(repeat=args.repeat, scale=args.budget_scale)
    with closing(
        SqliteTimetableRepository(path=db, city=city),
    ) as repo, closing(SqliteTileRepository(path=tiles_path)) as tiles:
        pin = plain_pin(repo=repo, stop=args.stop)
        if pin is None:
            return 1
        drawn = first_departures(repo=repo, pin=pin, at=start)
        if not drawn:
            print(
                no_departures(
                    repo=repo,
                    stop=args.stop,
                    start=start,
                    asked=args.at is not None,
                ),
                file=sys.stderr,
            )
            return 1
        pin = replace(pin, kind=drawn[0].facts.kind)
        choice, inputs = loading_stages(
            repo=repo,
            city=city,
            pin=pin,
            drawn=drawn,
            at=start,
            timing=timing,
        )
        counts = drawing_stages(
            tiles=tiles,
            city=city,
            choice=choice,
            inputs=inputs,
            timing=timing,
        )
    print(f'lines {counts[0]}, rings {counts[1]}')
    return OVER_BUDGET if timing.over else 0


if __name__ == '__main__':
    sys.exit(main())
