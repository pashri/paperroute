"""Tests for state: shapes."""

from dataclasses import replace
from datetime import datetime, timedelta

from paperroute_device.core.kit_events import Tick
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.factories import START, pattern, snapshot, vehicle
from paperroute_testkit.core.kit_steps import step
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import make_shape
from paperroute_transit.core.map_view import MapView
from paperroute_transit.core.messages import (
    FetchFeed,
    LoadPatterns,
    LoadShapes,
    PatternsLoaded,
    PositionsFetched,
    ShapesLoaded,
)
from paperroute_transit.core.reducers import initial_state
from paperroute_transit.core.saved import LiveView, seeded
from paperroute_transit.core.state_model import State
from paperroute_transit.core.stop_view import SIDEBAR_ROWS, StopView
from paperroute_transit.core.tiles import CITY, ZOOMS
from tests.core.state.helpers import (
    BUS_POSITIONS,
    CENTRE,
    PIN,
    PRESS,
    effects_of,
    fresh,
    left_for_stop,
    loaded,
    map_zoom,
    turned,
)


def city_map(*, count: int = 1) -> State:
    """
    Show the Map at city zoom with shaped pin departures.

    Parameters
    ----------
    count : int
        Pin departures, each with its own shape.

    Returns
    -------
    State
        The state on the Map at 45 m/px.
    """
    state = loaded(state=fresh(centre=CENTRE), count=count, shaped=True)
    state, _ = step(state, PRESS)
    state, _ = turned(state=state, steps=-len(ZOOMS))
    assert ZOOMS[map_zoom(state=state)] == CITY
    return state


def stale_bus(*, trip_id: str, landed: datetime) -> PositionsFetched:
    """
    Land a bus download holding one vehicle unheard for ten minutes.

    Parameters
    ----------
    trip_id : str
        The vehicle's trip.
    landed : datetime
        When the download landed.

    Returns
    -------
    PositionsFetched
        The download, with the vehicle at the map centre.
    """
    bus = vehicle(
        timestamp=int((landed - timedelta(minutes=10)).timestamp()),
        trip_id=trip_id,
        point=CENTRE,
    )
    return PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=landed, vehicles=(bus,)),
    )


def stale_pattern_loaded(*, trip_id: str, shape_id: str) -> PatternsLoaded:
    """
    Load one stale trip's pattern with its own shape.

    Parameters
    ----------
    trip_id : str
        The trip.
    shape_id : str
        Its shape.

    Returns
    -------
    PatternsLoaded
        The pattern and its shape.
    """
    line = make_shape(shape_id=shape_id, points=[CENTRE, (0.0, 0.0)])
    return PatternsLoaded(
        trip_ids=frozenset({trip_id}),
        patterns={trip_id: pattern(trip_id=trip_id, shape_id=shape_id)},
        shapes={shape_id: line},
    )


def test_shapes_of_dropped_patterns_are_dropped() -> None:
    """A stale trip's shape goes with its pattern; the pin's shape stays."""
    state = city_map()
    pin_line = make_shape(shape_id='b:0', points=[CENTRE, (0.0, 0.0)])
    state, _ = step(state, ShapesLoaded(shapes={'b:0': pin_line}))
    state, _ = step(state, stale_bus(trip_id='gone', landed=START))
    state, _ = step(
        state,
        stale_pattern_loaded(trip_id='gone', shape_id='s:gone'),
    )
    assert set(state.map.shapes) == {'b:0', 's:gone'}
    state, _ = step(state, stale_bus(trip_id='other', landed=START))
    assert 'gone' not in state.map.patterns
    assert set(state.map.shapes) == {'b:0'}


def test_shapes_stay_bounded_at_city_zoom() -> None:
    """Stale trips coming and going never pile up shapes."""
    state = city_map()
    for index in range(20):
        landed = START + timedelta(seconds=index)
        trip_id = f'stale-{index}'
        state, effects = step(state, stale_bus(trip_id=trip_id, landed=landed))
        [load] = effects_of(effects=effects, kind=LoadPatterns)
        assert trip_id in load.trip_ids
        state, _ = step(
            state,
            stale_pattern_loaded(trip_id=trip_id, shape_id=f's:{index}'),
        )
        assert set(state.map.shapes) <= {'b:0', f's:{index}'}


def test_unneeded_pin_shapes_can_be_asked_for_again() -> None:
    """A pin shape dropped with its departure is requested when it returns."""
    state = city_map(count=2)
    lines = {
        shape_id: make_shape(shape_id=shape_id, points=[CENTRE, (0.0, 0.0)])
        for shape_id in ('b:0', 'b:1')
    }
    state, _ = step(state, ShapesLoaded(shapes=lines))
    state = loaded(state=state, count=1, shaped=True)
    assert set(state.map.shapes) == {'b:0'}
    assert state.map.requested_shapes == {'b:0'}
    state = loaded(state=state, count=2, shaped=True)
    assert state.map.requested_shapes == {'b:0', 'b:1'}


def test_stale_patterns_wait_for_the_fetch_in_flight() -> None:
    """Old positions on return to the Map ask for no stale patterns."""
    state = city_map()
    bus = vehicle(timestamp=int(START.timestamp()), point=CENTRE)
    event = PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=START, vehicles=(bus,)),
    )
    state, _ = step(state, event)
    state = left_for_stop(state=state)
    later = START + timedelta(minutes=5)
    state, _ = step(state, Tick(now=later))
    state, effects = step(state, PRESS)
    fetch = FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)
    assert fetch in effects
    assert not any(
        't1' in load.trip_ids
        for load in effects_of(effects=effects, kind=LoadPatterns)
    )
    state, _ = step(state, Tick(now=later + timedelta(seconds=5)))
    assert 't1' not in state.map.pending_patterns
    landed = replace(
        event,
        snapshot=snapshot(fetched_at=later, vehicles=(bus,)),
    )
    _, effects = step(state, landed)
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert 't1' in load.trip_ids


def test_drawn_trip_keeps_its_pattern_while_its_vehicle_is_fresh() -> None:
    """A fresh vehicle on the next trip leaves its pattern for the frame."""
    state = city_map()
    [pin] = state.board.departures
    state, _ = step(
        state,
        PatternsLoaded(
            trip_ids=frozenset({pin.trip_id}),
            patterns={pin.trip_id: pattern(trip_id=pin.trip_id)},
            shapes={},
        ),
    )
    bus = vehicle(
        timestamp=int(START.timestamp()), trip_id=pin.trip_id, point=CENTRE,
    )
    state, _ = step(
        state,
        PositionsFetched(
            feed=BUS_POSITIONS,
            snapshot=snapshot(fetched_at=START, vehicles=(bus,)),
        ),
    )
    assert pin.trip_id in state.map.patterns


def test_stale_pin_trip_is_asked_for_at_once() -> None:
    """
    A pin trip with no pattern is asked for while a fetch is in flight.

    The state jumps from the Map to the Stop view, since pressing round
    passes the Strip, which loads every strip trip's pattern.
    """
    state = city_map()
    [pin] = state.board.departures
    bus = vehicle(
        timestamp=int(START.timestamp()), trip_id=pin.trip_id, point=CENTRE,
    )
    state, _ = step(
        state,
        PositionsFetched(
            feed=BUS_POSITIONS,
            snapshot=snapshot(fetched_at=START, vehicles=(bus,)),
        ),
    )
    state = replace(state, view=LiveView.STOP)
    state, _ = step(state, Tick(now=START + timedelta(minutes=5)))
    state = replace(
        state,
        map=replace(state.map, patterns={}, pending_patterns=frozenset()),
    )
    _, effects = step(state, PRESS)
    assert FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS) in effects
    [load] = effects_of(effects=effects, kind=LoadPatterns)
    assert pin.trip_id in load.trip_ids


def test_stop_view_tag_counts_the_pins() -> None:
    """A single pin's tag reads pin 1/1."""
    state, _ = step(fresh(), Tick(now=START))
    assert isinstance(state.last_view, StopView)
    assert state.last_view.tag.text == 'pin 1/1'


def test_no_destination_map_caps_patterns() -> None:
    """A place with 500 departures asks for no more than the sidebar shows."""
    pin = replace(
        PIN,
        origin=replace(PIN.origin, point=CENTRE),
        destination=None,
    )
    state = initial_state(
        saved=seeded(pin=pin, city=SYDNEY),
        now=START,
        city=SYDNEY,
    )
    state = loaded(state=state, count=500)
    _, effects = step(state, PRESS)
    loads = effects_of(effects=effects, kind=LoadPatterns)
    assert len(loads) == 1
    assert 0 < len(loads[0].trip_ids) <= SIDEBAR_ROWS


def no_destination_map(*, count: int) -> State:
    """
    Show the Map for a place with no destination and shaped departures.

    Parameters
    ----------
    count : int
        Departures, each with its own shape.

    Returns
    -------
    State
        The state before the Map's first request.
    """
    pin = replace(
        PIN,
        origin=replace(PIN.origin, point=CENTRE),
        destination=None,
    )
    state = initial_state(
        saved=seeded(pin=pin, city=SYDNEY),
        now=START,
        city=SYDNEY,
    )
    return loaded(state=state, count=count, shaped=True)


def test_no_destination_map_asks_for_and_keeps_only_sidebar_shapes() -> None:
    """500 departures ask for the sidebar's shapes; other shapes go."""
    state = no_destination_map(count=500)
    state, effects = step(state, PRESS)
    [load] = effects_of(effects=effects, kind=LoadShapes)
    assert load.shape_ids == {f'b:{i}' for i in range(SIDEBAR_ROWS)}
    line = make_shape(shape_id='b:400', points=[CENTRE, (0.0, 0.0)])
    state, _ = step(state, ShapesLoaded(shapes={'b:400': line}))
    assert not state.map.shapes
    assert state.map.requested_shapes == load.shape_ids


def test_no_destination_map_ignores_the_stop_scroll() -> None:
    """The sidebar and the drawn departures both start at the board's top."""
    state = no_destination_map(count=30)
    state, _ = turned(state=state, steps=4)
    assert state.scroll == 4
    state, effects = step(state, PRESS)
    assert isinstance(state.last_view, MapView)
    [load] = effects_of(effects=effects, kind=LoadShapes)
    assert load.shape_ids == {f'b:{i}' for i in range(SIDEBAR_ROWS)}
    assert [row.leaves_at for row in state.last_view.sidebar] == [
        f'8:{3 * (i + 1):02d}' for i in range(SIDEBAR_ROWS)
    ]
