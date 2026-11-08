"""Departures, trip patterns, vehicle positions and strip inputs."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Final

from paperroute.city import SYDNEY, SYDNEY_ZONE, SydneyFeed, SydneyMode
from paperroute_transit.core.geo import Point
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import (
    CarryOn,
    Departure,
    Pin,
    StopPlace,
    TripFacts,
)
from paperroute_transit.core.modes import line_modes
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    RunTail,
    StripKey,
    build_reach,
)
from paperroute_transit.core.reach_layout import LineExit
from paperroute_transit.core.saved import Saved
from paperroute_transit.core.state import initial_state
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import LOADING_WAIT
from paperroute_transit.core.stop_view import (
    NOT_PINNED,
    StopInputs,
    StopView,
    build_stop_view,
)
from paperroute_transit.core.strip_model import StripInputs

START = datetime(2026, 10, 16, 8, 0, tzinfo=SYDNEY_ZONE)
STALE_LIMIT: Final[timedelta] = timedelta(minutes=2)
EIGHT: int = 8 * 3600  # seconds into the service day
TEN_MINUTES: int = 600  # seconds
TRAIN: Final[StripKey] = StripKey(
    station='C', kind=SydneyMode.TRAIN, routes=(),
)
POINTS: Final[dict[str, Point]] = {
    'C': (0.0, 0.0),
    'E1': (0.0, -1000.0),
    'E2': (0.0, -2000.0),
    'J': (0.0, 1000.0),
    'X': (5000.0, 0.0),
}
EAST: Final[tuple[str, ...]] = ('C', 'E1', 'E2')
LINE: Final[dict[str, Point]] = {
    'A': (0.0, 0.0),
    'B': (0.0, -1000.0),
    'C': (0.0, -2000.0),
    'D': (0.0, -3000.0),
}
T2_ON: Final[CarryOn] = CarryOn(
    route='T2', headsign='Leppington', trip_id='b',
)


def begun(*, saved: Saved, now: datetime = START) -> State:
    """
    Make a stored state whose app started `LOADING_WAIT` ago.

    Its first frame is drawn at once, even before departures load.

    Parameters
    ----------
    saved : Saved
        What the device kept.
    now : datetime
        The time now.

    Returns
    -------
    State
        The state.
    """
    state = initial_state(saved=saved, now=now, stored=True, city=SYDNEY)
    refresh = replace(state.refresh, started_at=now - LOADING_WAIT)
    return replace(state, refresh=refresh)


def place(
    stop_id: str, *, name: str = '', point: Point | None = None
) -> StopPlace:
    """
    A stop or station, bare unless a name or point is given.

    Parameters
    ----------
    stop_id : str
        Stop or station ID.
    name : str
        Name.
    point : Point | None
        Position in metres.

    Returns
    -------
    StopPlace
        The place.
    """
    return StopPlace(stop_id=stop_id, name=name, point=point)


def pin_of(origin: str, destination: str | None) -> Pin:
    """
    A pin between two bare places.

    Parameters
    ----------
    origin : str
        From stop or station ID.
    destination : str | None
        Destination stop or station ID, or None for no Destination.

    Returns
    -------
    Pin
        The pin.
    """
    return Pin(
        origin=place(origin),
        destination=None if destination is None else place(destination),
    )


def pin_at(origin: str, *, x: float) -> Pin:
    """
    A pin with no Destination at a named From with a point.

    Parameters
    ----------
    origin : str
        From stop ID, also used as its name.
    x : float
        The From's easting in metres; its northing is 0.

    Returns
    -------
    Pin
        The pin.
    """
    return Pin(origin=place(origin, name=origin, point=(x, 0.0)))


def departure(
    *,
    departs: datetime,
    ride: int = 30,
    route: str = '606',
    mode: SydneyFeed = SydneyFeed.BUS,
    trip_id: str | None = None,
    headsign: str = 'Parramatta Station',
    platform: str = '',
    kind: SydneyMode | None = None,
    then: CarryOn | None = None,
) -> Departure:
    """
    A departure from stop sequence 5 to 20.

    Parameters
    ----------
    departs : datetime
        Scheduled departure.
    ride : int
        Minutes to the destination.
    route : str
        Route short name.
    mode : SydneyFeed
        Mode.
    trip_id : str | None
        Trip ID; defaults to one derived from the departure time.
    headsign : str
        Headsign.
    platform : str
        Platform or stand.
    kind : SydneyMode | None
        Kind of route.
    then : CarryOn | None
        The service the vehicle runs next.

    Returns
    -------
    Departure
        The departure.
    """
    return Departure(
        trip_id=trip_id or f'trip-{departs:%H%M}',
        aliases=(),
        route=route,
        source=mode,
        headsign=headsign,
        platform=platform,
        origin_seq=5,
        origin_stop='o',
        destination_seq=20,
        destination_stop='d',
        departs=departs,
        arrives=departs + timedelta(minutes=ride),
        service_day=departs.date(),
        facts=TripFacts(kind=kind, then=then),
    )


def calls(
    *,
    stations: Sequence[str],
    start: int = EIGHT,
    step: int = 300,
    points: Mapping[str, Point] | None = None,
) -> tuple[PatternStop, ...]:
    """
    Calls at stations a fixed time apart, with no dwell.

    Parameters
    ----------
    stations : Sequence[str]
        Station IDs in running order.
    start : int
        Seconds into the service day at the first station.
    step : int
        Seconds between stations.
    points : Mapping[str, Point] | None
        Station positions in metres; a missing one is None.

    Returns
    -------
    tuple[PatternStop, ...]
        The calls.
    """
    return tuple(
        PatternStop(
            arrives=start + step * index,
            departs=start + step * index,
            point=(points or {}).get(station),
            station=station,
        )
        for index, station in enumerate(stations)
    )


def pattern(
    *,
    trip_id: str = 't1',
    route: str = '606',
    shape_id: str | None = None,
    stops: tuple[PatternStop, ...] | None = None,
    days: tuple[date, ...] | None = None,
    along: tuple[float, ...] | None = None,
) -> TripPattern:
    """
    A bus pattern that runs 1 km east from the origin, 8:00 to 8:10.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    route : str
        Route short name.
    shape_id : str | None
        Shape ID.
    stops : tuple[PatternStop, ...] | None
        Calls; defaults to two stops at (0, 0) and (1000, 0).
    days : tuple[date, ...] | None
        Running dates; defaults to the day of `START`.
    along : tuple[float, ...] | None
        Metres along the shape at each stop.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return TripPattern(
        trip_id=trip_id,
        route=route,
        source=SydneyFeed.BUS,
        shape_id=shape_id,
        stops=(
            stops
            if stops is not None
            else (
                PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
                PatternStop(
                    arrives=EIGHT + TEN_MINUTES,
                    departs=EIGHT + TEN_MINUTES,
                    point=(1000.0, 0.0),
                ),
            )
        ),
        days=days if days is not None else (START.date(),),
        along=along,
    )


def vehicle(
    *,
    timestamp: int,
    trip_id: str = 't1',
    label: str = '606',
    point: Point = (0.0, 0.0),
    bearing: float | None = None,
) -> VehiclePosition:
    """
    A vehicle position.

    Parameters
    ----------
    timestamp : int
        When the vehicle reported, in Unix seconds.
    trip_id : str
        Trip ID.
    label : str
        Display label.
    point : Point
        Position in metres.
    bearing : float | None
        Heading in degrees.

    Returns
    -------
    VehiclePosition
        The position.
    """
    return VehiclePosition(
        trip_id=trip_id,
        route_id=f'r_{label}',
        label=label,
        point=point,
        bearing=bearing,
        timestamp=timestamp,
        start_date=None,
    )


def snapshot(
    *,
    fetched_at: datetime,
    vehicles: tuple[VehiclePosition, ...],
) -> PositionsSnapshot:
    """
    A positions download whose header time is its fetch time.

    Parameters
    ----------
    fetched_at : datetime
        When the feed was downloaded.
    vehicles : tuple[VehiclePosition, ...]
        Vehicles in the feed.

    Returns
    -------
    PositionsSnapshot
        The snapshot.
    """
    return PositionsSnapshot(
        fetched_at=fetched_at,
        header_time=int(fetched_at.timestamp()),
        vehicles=vehicles,
    )


def example_reach() -> Reach:
    """
    Build the reach W2 W1 R3 R2 R1 J C E1 E2, with C the From.

    Returns
    -------
    Reach
        The reach; `W1_1` is a platform of W1.
    """
    keys = ('C', 'J', 'W1', 'W2', 'R1', 'R2', 'R3', 'E1', 'E2')
    places = {
        key: StopPlace(
            stop_id=key, name=f'{key} Station', point=POINTS.get(key),
        )
        for key in keys
    }
    return build_reach(data=ReachInput(
        origin=places['C'],
        tails=(
            RunTail(before=(), after=('J', 'W1', 'W2'), trips=6),
            RunTail(before=(), after=('J', 'R1', 'R2', 'R3'), trips=3),
            RunTail(before=(), after=('E1', 'E2'), trips=8),
        ),
        places=places,
        station_of={'W1_1': 'W1', **{key: key for key in keys}},
    ))


def train(
    *, minutes: int, trip_id: str, kind: SydneyMode = SydneyMode.TRAIN,
) -> Departure:
    """
    Make a departure from C some minutes after 8:00.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00.
    trip_id : str
        Trip ID.
    kind : SydneyMode
        Kind of route.

    Returns
    -------
    Departure
        The departure.
    """
    return departure(
        departs=START + timedelta(minutes=minutes),
        trip_id=trip_id,
        route='T1',
        kind=kind,
    )


def line_pattern(
    *, trip_id: str, stations: Sequence[str], minutes: int,
) -> TripPattern:
    """
    Make a pattern calling every 3 minutes from some minutes after 8:00.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    stations : Sequence[str]
        Stations in order.
    minutes : int
        Minutes after 8:00 at the first station.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return pattern(
        trip_id=trip_id,
        route='T1',
        stops=calls(
            stations=stations,
            start=EIGHT + minutes * 60,
            step=180,
            points=POINTS,
        ),
    )


def live(
    *, item: Departure, late: int = 0, cancelled: bool = False,
) -> LiveDeparture:
    """
    Wrap a departure with a delay.

    Parameters
    ----------
    item : Departure
        Departure.
    late : int
        Minutes late.
    cancelled : bool
        Whether it is cancelled.

    Returns
    -------
    LiveDeparture
        The departure, shifted by the delay.
    """
    return LiveDeparture(
        departure=item,
        departs=item.departs + timedelta(minutes=late),
        arrives=item.arrives + timedelta(minutes=late),
        live=late != 0,
        cancelled=cancelled,
    )


def stop_view_of(
    *,
    departures: Sequence[LiveDeparture],
    now: datetime,
    destination: str | None = None,
) -> StopView:
    """
    Build the Stop view of some departures from C.

    Parameters
    ----------
    departures : Sequence[LiveDeparture]
        Departures.
    now : datetime
        Current time.
    destination : str | None
        Destination name.

    Returns
    -------
    StopView
        The view.
    """
    return build_stop_view(inputs=StopInputs(
        origin='C Station',
        destination=destination,
        departures=tuple(departures),
        now=now,
        zone=SYDNEY_ZONE,
        stale_after=STALE_LIMIT,
        city=SYDNEY,
        scroll=0,
        live_since=now,
        has_feed=True,
    ))


def inputs_of(
    *,
    reach: Reach | None,
    departures: Sequence[LiveDeparture] = (),
    patterns: Mapping[str, TripPattern | None] | None = None,
    positions: Mapping[str, PositionsSnapshot] | None = None,
    now: datetime = START - timedelta(minutes=2),
    destination: str | None = None,
) -> StripInputs:
    """
    Make Strip inputs with the usual defaults.

    Parameters
    ----------
    reach : Reach | None
        The reach.
    departures : Sequence[LiveDeparture]
        Departures from C.
    patterns : Mapping[str, TripPattern | None] | None
        Loaded patterns.
    positions : Mapping[str, PositionsSnapshot] | None
        Positions downloads.
    now : datetime
        Current time.
    destination : str | None
        Destination stop or station ID.

    Returns
    -------
    StripInputs
        The inputs.
    """
    return StripInputs(
        reach=reach,
        unavailable=None,
        departures=tuple(departures),
        patterns=patterns or {},
        positions=positions or {},
        now=now,
        zone=SYDNEY_ZONE,
        stale_after=STALE_LIMIT,
        line_kinds=line_modes(modes=SYDNEY.modes),
        scroll=0,
        cursor=None,
        picking=None,
        destination=destination,
        tag=NOT_PINNED,
        stop_view=stop_view_of(departures=departures, now=now),
        key=TRAIN,
    )


def east_inputs(
    *, at: Point | None, reported: datetime, now: datetime,
) -> StripInputs:
    """
    Make inputs with one eastbound train leaving C at 8:00.

    Parameters
    ----------
    at : Point | None
        Where its vehicle reported, or None for no vehicle.
    reported : datetime
        When the vehicle reported.
    now : datetime
        Current time.

    Returns
    -------
    StripInputs
        The inputs.
    """
    vehicles = () if at is None else (vehicle(
        timestamp=int(reported.timestamp()), trip_id='e', point=at,
    ),)
    return inputs_of(
        reach=example_reach(),
        departures=[live(item=train(minutes=0, trip_id='e'))],
        patterns={
            'e': line_pattern(trip_id='e', stations=EAST, minutes=0),
        },
        positions={'trains': snapshot(fetched_at=now, vehicles=vehicles)},
        now=now,
    )


def line_reach(*, off: bool = False) -> Reach:
    """
    Build the reach A B C D down one line, with A the From.

    Parameters
    ----------
    off : bool
        Make D an exit row off C instead of a station on the line.

    Returns
    -------
    Reach
        The reach.
    """
    places = {
        key: StopPlace(stop_id=key, name=f'{key} Station', point=point)
        for key, point in LINE.items()
    }
    return build_reach(data=ReachInput(
        origin=places['A'],
        tails=(
            RunTail(
                before=(), after=('B', 'C') if off else ('B', 'C', 'D'),
                trips=6,
            ),
        ),
        places=places,
        station_of={key: key for key in LINE},
        exits=(
            (LineExit(junction='C', off='D', lines=('T2',), trips=3),)
            if off
            else ()
        ),
    ))


def carry_on_inputs(
    *,
    now: datetime,
    off: bool = False,
    next_loaded: bool = True,
    vehicles: tuple[VehiclePosition, ...] = (),
) -> StripInputs:
    """
    Make inputs with a T8 from A to C that carries on there as a T2 to D.

    Trip a leaves A at 8:00 and reaches C at 8:10; trip b, its carry-on,
    leaves C at 8:12 and reaches D at 8:17.

    Parameters
    ----------
    now : datetime
        Current time.
    off : bool
        Make D an exit row off C.
    next_loaded : bool
        Load trip b's pattern.
    vehicles : tuple[VehiclePosition, ...]
        Vehicles in the positions feed.

    Returns
    -------
    StripInputs
        The inputs over `line_reach`.
    """
    a = pattern(
        trip_id='a', route='T8',
        stops=calls(stations='ABC', start=EIGHT, points=LINE),
    )
    b = pattern(
        trip_id='b', route='T2',
        stops=calls(stations='CD', start=EIGHT + 720, points=LINE),
    )
    item = departure(
        departs=START, trip_id='a', route='T8', mode=SydneyFeed.TRAIN,
        kind=SydneyMode.TRAIN, then=T2_ON,
    )
    return inputs_of(
        reach=line_reach(off=off),
        departures=[live(item=item)],
        patterns={'a': a, 'b': b} if next_loaded else {'a': a},
        positions={'trains': snapshot(fetched_at=now, vehicles=vehicles)},
        now=now,
    )
