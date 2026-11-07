"""Strip inputs, reaches and reducer runs shared by the Strip tests."""

from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from paperroute.core.geo import Point
from paperroute.core.map_view import DEFAULT_ZOOM
from paperroute.core.messages import (
    DeparturesLoaded,
    Effect,
    Event,
    Knob,
    KnobTurned,
)
from paperroute.core.model import Departure, Mode, Pin, RouteKind, StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind, StripKey
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state import initial_state, step
from paperroute.core.state_model import State
from paperroute.core.stop_view import HORIZON
from paperroute.core.strip_model import StripInputs, StripRow
from paperroute.core.strip_view import build_strip_view
from paperroute_testkit.core.factories import (
    EAST,
    START,
    departure,
    example_reach,
    inputs_of,
    line_pattern,
    live,
    place,
    train,
)
from paperroute_testkit.core.steps import step_answered

WEST: Final[tuple[str, ...]] = ('C', 'J', 'W1', 'W2')
SOUTH: Final[tuple[str, ...]] = ('C', 'J', 'R1', 'R2', 'R3')
CENTRE: Final[Point] = (-17_000.0, 1_000.0)
PIN: Final[Pin] = Pin(
    origin=place('C', name='Central', point=CENTRE),
    destination=place('S5', name='S5'),
    kind=RouteKind.TRAIN,
)
KEY: Final[StripKey] = StripKey(
    station='C', kind=RouteKind.TRAIN, routes=(),
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
            mode=Mode.TRAIN,
            route='T1',
            trip_id=f'train-{i}',
            kind=RouteKind.TRAIN,
        )
        for i in range(count)
    )


def on_view(
    *,
    view: LiveView,
    pin: Pin = PIN,
    departures: tuple[Departure, ...] | None = None,
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

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State and effects after the load.
    """
    saved = Saved(
        pins=(pin,), current=0, unpinned=None, view=view,
    )
    state = initial_state(saved=saved, now=START, stored=True)
    return step_answered(
        state=state,
        event=DeparturesLoaded(
            pin=pin,
            departures=trains() if departures is None else departures,
            until=START + HORIZON,
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
