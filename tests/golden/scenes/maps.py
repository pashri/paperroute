"""Golden map scenes."""

from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

from paperroute_sydney.city import SydneyFeed, SydneyMode
from paperroute_testkit.core.connection_factories import (
    LEG1,
    LEG2,
    TWO_WAY,
    at,
)
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    pattern,
    place,
    snapshot,
    vehicle,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import on_view
from paperroute_transit.core.chains import Legs, leg_key
from paperroute_transit.core.connection_model import way_legs
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.messages import (
    DayLinesLoaded,
    DeparturesLoaded,
    PositionsFetched,
    ShapesLoaded,
)
from paperroute_transit.core.model import Pin
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.saved import LiveView, MapFraming
from paperroute_transit.core.state_model import State
from tests.core.chains.helpers import TRAINS
from tests.core.state.helpers import BUS_POSITIONS, CENTRE, map_opened
from tests.core.test_connection_screens import started
from tests.golden.scenes.base import (
    APPROACH,
    DAY_STOPS,
    LABEL,
    NORTH_EAST,
    ROUTE,
    TWO_CHANGES,
)
from tests.golden.scenes.strips import measured, patterns_loaded


def map_scene() -> State:
    """
    Show the Map with a route line and a live bus.

    Returns
    -------
    State
        State at `START`.
    """
    state = map_opened(centre=CENTRE)
    line = make_shape(shape_id='b:0', points=ROUTE)
    state, _ = step(state, ShapesLoaded(shapes={'b:0': line}))
    bus = vehicle(timestamp=int(START.timestamp()), point=ROUTE[1])
    positions = snapshot(fetched_at=START, vehicles=(bus,))
    state, _ = step(
        state, PositionsFetched(feed=BUS_POSITIONS, snapshot=positions),
    )
    return state


def map_framed(*, framing: MapFraming) -> State:
    """
    Show the Map scene's pin with every trip's pattern, framed one way.

    Parameters
    ----------
    framing : MapFraming
        The map framing setting.

    Returns
    -------
    State
        State at `START`; each 606 calls every 150 seconds from 7.5
        minutes before the From to 5 minutes after, so its timetable
        marker sits between stops.
    """
    state = map_scene()
    board = replace(state.settings.board, map_framing=framing)
    state = replace(state, settings=replace(state.settings, board=board))
    patterns: dict[str, TripPattern | None] = {
        item.trip_id: pattern(
            trip_id=item.trip_id,
            stops=calls(
                stations=tuple(APPROACH),
                start=(
                    EIGHT
                    + int((item.departs - START).total_seconds())
                    - 450
                ),
                step=150,
                points=APPROACH,
            ),
        )
        for item in state.board.departures
    }
    return patterns_loaded(state=state, patterns=patterns)


def line_scene() -> State:
    """
    Show a bus pin with no Destination and the day's lines at its stop.

    Returns
    -------
    State
        State at `START`; the 606 comes from the west so its dashes run
        up to the From and its ride is solid after it, the 610 leaves
        north-east, and the 620 passes north to south, loaded as a day
        line and drawn dashed. Every pattern is measured along its shape.
    """
    plain = Pin(
        origin=place('C', name='Hollis St', point=CENTRE),
        kind=SydneyMode.BUS,
    )
    runs = {
        'a': ('606', 'a:0', 3, APPROACH),
        'b': ('610', 'b:0', 6, NORTH_EAST),
        'x': ('620', 'x:0', 9, DAY_STOPS),
    }
    shapes = {
        trip: make_shape(shape_id=shape_id, points=list(points.values()))
        for trip, (_, shape_id, _, points) in runs.items()
    }
    made = {
        trip: replace(
            departure(
                departs=START + timedelta(minutes=minutes),
                route=route,
                mode=SydneyFeed.BUS,
                kind=SydneyMode.BUS,
                trip_id=trip,
            ),
            shape_id=shape_id,
        )
        for trip, (route, shape_id, minutes, _) in runs.items()
    }
    state, _ = on_view(
        view=LiveView.MAP,
        pin=plain,
        departures=(made['a'], made['b']),
    )
    held = state.map.day_lines
    assert held is not None
    state, _ = step(
        state, DayLinesLoaded(request=held.request, departures=(made['x'],)),
    )
    patterns: dict[str, TripPattern | None] = {
        trip: measured(
            made=pattern(
                trip_id=trip,
                route=runs[trip][0],
                stops=calls(
                    stations=tuple(runs[trip][3]),
                    start=(
                        EIGHT
                        + int((made[trip].departs - START).total_seconds())
                        - 150 * list(runs[trip][3]).index('C')
                    ),
                    step=150,
                    points=runs[trip][3],
                ),
            ),
            line=line,
        )
        for trip, line in shapes.items()
    }
    return patterns_loaded(
        state=state, patterns=patterns, shapes=list(shapes.values()),
    )


def destination_scene() -> State:
    """
    Show a bus pin with a Destination on its route, at the default zoom.

    Returns
    -------
    State
        State at `START`; the 606 runs from the west through the From to
        the Destination just past `ROUTE[1]`, so its From and To are lettered
        A and B and its ride between them is solid.
    """
    beyond = (
        ROUTE[1][0] + (ROUTE[2][0] - ROUTE[1][0]) / 4,
        ROUTE[1][1] + (ROUTE[2][1] - ROUTE[1][1]) / 4,
    )
    stops = {
        name: point for name, point in APPROACH.items() if name != 'E2'
    }
    stops['D'] = beyond
    stops['E2'] = ROUTE[2]
    pin = Pin(
        origin=place('C', name='Hollis St', point=CENTRE),
        destination=place('D', name=LABEL, point=beyond),
        kind=SydneyMode.BUS,
    )
    line = make_shape(shape_id='a:0', points=list(stops.values()))
    made = replace(
        departure(
            departs=START + timedelta(minutes=3),
            route='606',
            mode=SydneyFeed.BUS,
            kind=SydneyMode.BUS,
            trip_id='a',
        ),
        shape_id='a:0',
    )
    state, _ = on_view(view=LiveView.MAP, pin=pin, departures=(made,))
    run = measured(
        made=pattern(
            trip_id='a',
            route='606',
            stops=calls(
                stations=tuple(stops),
                start=EIGHT + 180 - 150 * list(stops).index('C'),
                step=150,
                points=stops,
            ),
        ),
        line=line,
    )
    return patterns_loaded(state=state, patterns={'a': run}, shapes=[line])


def map_panes() -> State:
    """
    Show a two-change connection pin's Map, one pane per change.

    Returns
    -------
    State
        State at 7:30 on the connection's day.
    """
    state = started(view=LiveView.MAP, pins=(TWO_CHANGES,))
    lists = (LEG1, LEG2, TRAINS)
    legs = way_legs(pin=TWO_CHANGES, way=TWO_WAY)
    state, _ = step(state, DeparturesLoaded(
        pin=TWO_CHANGES,
        departures=(),
        until=at(10, 30),
        legs=Legs(lists=MappingProxyType({
            leg_key(leg=leg): items for leg, items in zip(legs, lists)
        })),
    ))
    return state
