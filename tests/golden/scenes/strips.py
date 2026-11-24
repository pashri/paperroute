"""Golden strip and connection scenes."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Final

from paperroute_testkit.city_setting import city_under_test, train_mode
from paperroute_testkit.core.factories import (
    EAST,
    POINTS,
    example_reach,
    line_pattern,
    place,
    train,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import (
    KEY,
    PIN,
    TRAIN_TRIP,
    on_view,
    train_departure,
    train_pattern,
    train_reach_example,
)
from paperroute_transit.core.geo import Shape, make_shape
from paperroute_transit.core.messages import PatternsLoaded, StripLoaded
from paperroute_transit.core.model import Pin
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.patterns import TripPattern, measure
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_model import State
from paperroute_transit.core.strip_state import strip_key
from tests.core.test_connection import late_legs
from tests.core.test_connection_screens import started, with_legs
from tests.core.test_connection_strip import PATTERNS as LEG_PATTERNS
from tests.golden.scenes.base import STRIP_TRIPS


def strip_scene(*, view: LiveView) -> State:
    """
    Show trains on a branching line on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.

    Returns
    -------
    State
        State at `START`, with the reach and every train's pattern.
    """
    departures = tuple(
        train(minutes=minutes, trip_id=trip)
        for trip, (_, minutes) in STRIP_TRIPS.items()
    )
    pin = replace(
        PIN,
        destination=None,
        origin=place('C', name='Central', point=POINTS['C']),
    )
    state, _ = on_view(view=view, pin=pin, departures=departures)
    state, _ = step(state, StripLoaded(key=KEY, reach=example_reach()))
    line = make_shape(
        shape_id='strip',
        points=[POINTS[station] for station in ('J', *EAST)],
    )
    patterns: dict[str, TripPattern | None] = {
        trip: measured(
            made=line_pattern(
                trip_id=trip,
                stations=stations,
                minutes=minutes,
            ),
            line=line,
        )
        for trip, (stations, minutes) in STRIP_TRIPS.items()
    }
    return patterns_loaded(state=state, patterns=patterns, shapes=[line])


def train_strip_scene(*, destination: str) -> State:
    """
    Show a train Strip following one train to a Destination.

    Parameters
    ----------
    destination : str
        The Destination's station ID and name.

    Returns
    -------
    State
        State at `START` on the Strip, with the train example's reach
        and the followed train's pattern.
    """
    pin = Pin(
        origin=place('Central', name='Central'),
        destination=place(destination, name=destination),
        kind=train_mode(),
    )
    key = strip_key(pin=pin, end_points=end_point_modes(
        modes=city_under_test().modes,
    ))
    state, _ = on_view(
        view=LiveView.STRIP, pin=pin, departures=(train_departure(),),
    )
    state, _ = step(
        state, StripLoaded(key=key, reach=train_reach_example()),
    )
    return patterns_loaded(
        state=state, patterns={TRAIN_TRIP: train_pattern()},
    )


def measured(*, made: TripPattern, line: Shape) -> TripPattern:
    """
    Put a pattern on a shape, measuring each call along it.

    Parameters
    ----------
    made : TripPattern
        The pattern.
    line : Shape
        The shape it runs along.

    Returns
    -------
    TripPattern
        The pattern naming the shape, with metres along at each call;
        unmeasured when a call has no point.
    """
    return measure(pattern=replace(made, shape_id=line.shape_id), shape=line)


def patterns_loaded(
    *,
    state: State,
    patterns: dict[str, TripPattern | None],
    shapes: Sequence[Shape] = (),
) -> State:
    """
    Load some trips' patterns.

    Parameters
    ----------
    state : State
        State.
    patterns : dict[str, TripPattern | None]
        Patterns by trip ID.
    shapes : Sequence[Shape]
        Shapes loaded with them.

    Returns
    -------
    State
        State after the load.
    """
    state, _ = step(state, PatternsLoaded(
        trip_ids=frozenset(patterns), patterns=patterns,
        shapes={line.shape_id: line for line in shapes},
    ))
    return state


def connection_strip(*, view: LiveView) -> State:
    """
    Show the Work connection pin's legs on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.

    Returns
    -------
    State
        State at 7:30 on the connection's day, with both legs' patterns.
    """
    return patterns_loaded(
        state=connection(view=view), patterns=dict(LEG_PATTERNS),
    )


def connection(*, view: LiveView) -> State:
    """
    Show the Work connection pin on a Live view.

    Parameters
    ----------
    view : LiveView
        The Live view.

    Returns
    -------
    State
        State at 7:30 on the connection's day.
    """
    return with_legs(state=started(view=view))


EASY_LATES: Final[Mapping[str, int]] = MappingProxyType({
    'a0733': 4, 'b0746': 2, 'a0822': -2,
})


TIGHT_LATES: Final[Mapping[str, int]] = MappingProxyType({
    'a0733': 4, 'a0748': 4, 'a0822': -2,
})


MISSED_LATES: Final[Mapping[str, int]] = MappingProxyType({
    'a0733': 6, 'a0748': 6, 'a0842': 8,
})


def late_connection(*, view: LiveView, lates: Mapping[str, int]) -> State:
    """
    Show the Work connection pin once its feed has answered, then late.

    Parameters
    ----------
    view : LiveView
        The Stop or Strip view.
    lates : Mapping[str, int]
        Minutes late by trip ID in the second answer.

    Returns
    -------
    State
        State at 7:30 with the first offers remembered and the trips
        late.
    """
    state = (
        connection(view=view)
        if view is LiveView.STOP
        else connection_strip(view=view)
    )
    state = late_legs(state=state, lates={})
    return late_legs(state=state, lates=lates)
