"""Strip inputs, reaches and reducer runs shared by the Strip tests."""

from collections.abc import Sequence
from datetime import timedelta
from itertools import pairwise
from typing import Final

from paperroute_hardware.core.knob_events import Knob, KnobTurned
from paperroute_testkit.city_setting import city_under_test, train_mode
from paperroute_testkit.core.factories import (
    EAST,
    EIGHT,
    LINE_MODE,
    LINE_SOURCE,
    STALE_LIMIT,
    START,
    calls,
    departure,
    example_reach,
    inputs_of,
    line_pattern,
    live,
    pattern,
    place,
    train,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.steps import step_answered
from paperroute_transit.core.geo import Point
from paperroute_transit.core.map_view import DEFAULT_ZOOM
from paperroute_transit.core.messages import DeparturesLoaded, Effect, Event
from paperroute_transit.core.model import Departure, Pin, StopPlace
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    ReachRow,
    RowKind,
    StripKey,
)
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LiveView, Saved, default_settings
from paperroute_transit.core.state_model import State
from paperroute_transit.core.stop_view import HORIZON
from paperroute_transit.core.strip_model import StripInputs, StripRow
from paperroute_transit.core.strip_times import TripInputs
from paperroute_transit.core.strip_view import build_strip_view
from paperroute_transit.core.train_line import train_reach
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun

WEST: Final[tuple[str, ...]] = ('C', 'J', 'W1', 'W2')
SOUTH: Final[tuple[str, ...]] = ('C', 'J', 'R1', 'R2', 'R3')
CENTRE: Final[Point] = (-17_000.0, 1_000.0)
PIN: Final[Pin] = Pin(
    origin=place('C', name='Central', point=CENTRE),
    destination=place('S5', name='S5'),
    kind=LINE_MODE,
)
KEY: Final[StripKey] = StripKey(
    station='C', kind=LINE_MODE, routes=(),
)


TRAIN_LINE: Final[tuple[str, ...]] = (
    'Wynyard',
    'Town Hall',
    'Central',
    'Redfern',
    'Newtown',
    'Petersham',
    'Summer Hill',
    'Ashfield',
    'Burwood',
    'Strathfield',
    'Flemington',
    'Lidcombe',
)  # the followed train's calls; the first two are earlier calls
TRAIN_PASSED: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ('Redfern', 'Newtown'): ('Macdonaldtown',),
    ('Newtown', 'Petersham'): ('Stanmore',),
    ('Petersham', 'Summer Hill'): ('Lewisham',),
    ('Ashfield', 'Burwood'): ('Croydon',),
    ('Strathfield', 'Flemington'): ('Homebush',),
}  # the stations the train runs through between two calls
TRAIN_HOPS: Final[dict[tuple[str, str], Hop]] = {
    pair: Hop(passed=TRAIN_PASSED.get(pair, ()), inside=True)
    for pair in pairwise(TRAIN_LINE)
}
TRAIN_TRIP: Final[str] = 'train-t1'
SOUTH_COAST: Final[tuple[str, ...]] = (
    'Cronulla',
    'Waterfall',
    'Kiama',
    'Campbelltown',
    'Wollongong',
    'Bomaderry',
    'Port Kembla',
    'Helensburgh',
    'Thirroul',
)  # the places the train example's second exit names


def train_departure() -> Departure:
    """
    Make the followed train's departure: Central at 8:10.

    Returns
    -------
    Departure
        The departure, of the first end-point mode.
    """
    return departure(
        departs=START + timedelta(minutes=10),
        trip_id=TRAIN_TRIP,
        route='T1',
        kind=train_mode(),
    )


def train_pattern() -> TripPattern:
    """
    Make the followed train's pattern, calling every 3 minutes.

    Returns
    -------
    TripPattern
        Calls at `TRAIN_LINE`; Central is at 8:10.
    """
    return pattern(
        trip_id=TRAIN_TRIP,
        route='T1',
        stops=calls(
            stations=TRAIN_LINE,
            start=EIGHT + 600 - 180 * TRAIN_LINE.index('Central'),
            step=180,
        ),
    )


def train_runs() -> tuple[WeekdayRun, ...]:
    """
    Make the weekday runs that leave the followed train's line.

    Returns
    -------
    tuple[WeekdayRun, ...]
        Two for Revesby and Macarthur leaving at Redfern, and one for
        each place in `SOUTH_COAST` leaving at Newtown.
    """
    return (
        WeekdayRun(
            route='T2',
            stations=('Central', 'Redfern', 'Revesby'),
            trains=8,
        ),
        WeekdayRun(
            route='T2',
            stations=('Central', 'Redfern', 'Revesby', 'Macarthur'),
            trains=8,
        ),
        *(
            WeekdayRun(
                route='T4',
                stations=(
                    'Central', 'Redfern', 'Newtown', 'Sydenham', end,
                ),
                trains=6,
            )
            for end in SOUTH_COAST
        ),
    )


def train_input() -> ReachInput:
    """
    Make the train example's Reach input: Central and its network.

    Returns
    -------
    ReachInput
        The From, every place named by ID, and the hops and runs.
    """
    ids = {
        *TRAIN_LINE,
        *(stop for run in train_runs() for stop in run.stations),
        *(passed for hop in TRAIN_HOPS.values() for passed in hop.passed),
    }
    places = {key: place(key, name=key) for key in ids}
    return ReachInput(
        origin=places['Central'],
        tails=(),
        places=places,
        station_of={key: key for key in ids},
        network=TrainNetwork(hops=TRAIN_HOPS, runs=train_runs()),
    )


def train_trips() -> TripInputs:
    """
    Make the followed train's trip inputs at 8:00.

    Returns
    -------
    TripInputs
        One departure and its pattern, with no positions.
    """
    return TripInputs(
        departures=(live(item=train_departure()),),
        key=None,
        patterns={TRAIN_TRIP: train_pattern()},
        positions={},
        now=START,
        stale_after=STALE_LIMIT,
        zone=city_under_test().zone,
        station_of={},
        line_kinds=frozenset(),
    )


def train_reach_example(*, destination: str = 'Lidcombe') -> Reach:
    """
    Build a train Strip's Reach with the real row builder.

    Parameters
    ----------
    destination : str
        The Destination's station ID.

    Returns
    -------
    Reach
        Two earlier calls, Central, then 14 rows to Lidcombe with 5
        passed stations, and two exits: one for Revesby and Macarthur
        at Redfern and one needing three lines at Newtown.
    """
    trips = train_trips()
    return train_reach(
        data=train_input(),
        trips=trips,
        main=trips.departures[0],
        destination=destination,
    )


def long_reach() -> Reach:
    """
    Build a 40-row depth-0 reach with the From at row 20.

    Returns
    -------
    Reach
        Rows S0 to S39, tracks running away from S20.
    """
    return Reach(
        rows=tuple(
            ReachRow(
                place=StopPlace(stop_id=f'S{i}', name=f'S{i}', point=None),
                kind=RowKind.ORIGIN if i == 20 else RowKind.MAIN,
                depth=0,
                above=i < 20,
                parent=None if i == 20 else (i + 1 if i < 20 else i - 1),
            )
            for i in range(40)
        ),
        stations=39,
        station_of={},
    )


def three_trains() -> StripInputs:
    """
    Make inputs with one train each way from C at 8:00, 8:03 and 8:04.

    Returns
    -------
    StripInputs
        The inputs on the example reach, at 7:58.
    """
    trips = {'e': (EAST, 0), 'w': (WEST, 3), 'r': (SOUTH, 4)}
    return inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=minutes, trip_id=trip))
            for trip, (_, minutes) in trips.items()
        ],
        patterns={
            trip: line_pattern(trip_id=trip, stations=line, minutes=minutes)
            for trip, (line, minutes) in trips.items()
        },
    )


def row_named(*, rows: Sequence[StripRow], name: str) -> StripRow:
    """
    Find a row by name.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Rows.
    name : str
        Station name without ' Station'.

    Returns
    -------
    StripRow
        The row.
    """
    return next(row for row in rows if row.name == name)


def times(*, inputs: StripInputs) -> dict[str, str]:
    """
    Read each row's time.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.

    Returns
    -------
    dict[str, str]
        Time by row name.
    """
    view = build_strip_view(inputs=inputs)
    return {row.name: row.time for row in view.rows}


def trains(*, count: int = 3) -> tuple[Departure, ...]:
    """
    Make train departures five minutes apart from 8:05.

    Parameters
    ----------
    count : int
        Departures.

    Returns
    -------
    tuple[Departure, ...]
        Departures, each its own trip.
    """
    return tuple(
        departure(
            departs=START + timedelta(minutes=5 * (i + 1)),
            mode=LINE_SOURCE,
            route='T1',
            trip_id=f'train-{i}',
            kind=LINE_MODE,
        )
        for i in range(count)
    )


def on_view(
    *,
    view: LiveView,
    pin: Pin = PIN,
    departures: tuple[Departure, ...] | None = None,
    station: str = '',
) -> tuple[State, tuple[Effect, ...]]:
    """
    Start on a view and load departures there, answering the feeds.

    Parameters
    ----------
    view : LiveView
        The Live view to start on.
    pin : Pin
        The only pin, shown.
    departures : tuple[Departure, ...] | None
        Departures loaded; three trains by default.
    station : str
        The From's station, as the load read it.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects after the load.
    """
    saved = Saved(
        settings=default_settings(city=city_under_test()),
        pins=(pin,), current=0, unpinned=None, view=view,
    )
    state = initial_state(
        saved=saved, now=START, city=city_under_test(),
    )
    return step_answered(
        state=state,
        event=DeparturesLoaded(
            pin=pin,
            departures=trains() if departures is None else departures,
            until=START + HORIZON,
            station=station,
        ),
    )


def run(
    *, state: State, events: list[Event],
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply events in turn.

    Parameters
    ----------
    state : State
        State before.
    events : list[Event]
        Events.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State after, and every effect in order.
    """
    effects: tuple[Effect, ...] = ()
    for event in events:
        state, more = step(state, event)
        effects = (*effects, *more)
    return state, effects


def turn(*, steps: int) -> KnobTurned:
    """
    Turn the bottom knob.

    Parameters
    ----------
    steps : int
        Detents, clockwise positive.

    Returns
    -------
    KnobTurned
        The turn.
    """
    return KnobTurned(knob=Knob.BOTTOM, steps=steps)
