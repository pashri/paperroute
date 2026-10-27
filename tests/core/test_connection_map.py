"""Tests for the connection Map's model."""

from dataclasses import replace
from datetime import datetime
from typing import Final

from paperroute.core.connection_map import (
    BANNER_ROOM,
    build_connection_map,
    connection_inputs,
    fit_zoom,
    leg_line,
)
from paperroute.core.connection_model import Pair
from paperroute.core.geo import Shape, make_shape
from paperroute.core.map_view import DEFAULT_ZOOM, MapInputs
from paperroute.core.model import Way
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.saved import ConnectionMap
from paperroute.core.tiles import ZOOMS
from tests.core.connection_factories import WORK, WORK_CHANGE, at
from tests.core.factories import snapshot, vehicle
from tests.core.test_connection_strip import PAIRS, STOP

LEG_ONE: Final[Shape] = make_shape(
    shape_id='s606', points=((0.0, 900.0), (0.0, 450.0), (0.0, 0.0)),
)
LEG_TWO: Final[Shape] = make_shape(
    shape_id='s665',
    points=((144.0, 0.0), (500.0, -900.0), (800.0, -1900.0)),
)


def shaped(*, pairs: tuple[Pair, ...]) -> tuple[Pair, ...]:
    """
    Give each pair's legs the test shapes.

    Parameters
    ----------
    pairs : tuple[Pair, ...]
        Pairs.

    Returns
    -------
    tuple[Pair, ...]
        The pairs with shape IDs set.
    """
    return tuple(
        Pair(
            first=replace(
                pair.first,
                departure=replace(pair.first.departure, shape_id='s606'),
            ),
            second=None if pair.second is None else replace(
                pair.second,
                departure=replace(pair.second.departure, shape_id='s665'),
            ),
        )
        for pair in pairs
    )


SHAPED: Final[tuple[Pair, ...]] = shaped(pairs=PAIRS)


def inputs_at(*, now: datetime, zoom: int = DEFAULT_ZOOM) -> MapInputs:
    """
    Map inputs for the Work pin.

    Parameters
    ----------
    now : datetime
        Current time.
    zoom : int
        Knob zoom.

    Returns
    -------
    MapInputs
        The inputs, centred on the From.
    """
    return MapInputs(
        stop_view=STOP,
        departures=(),
        centre=(0.0, 900.0),
        zoom=zoom,
        positions={},
        patterns={},
        shapes={'s606': LEG_ONE, 's665': LEG_TWO},
        now=now,
    )


def test_leg_line_cuts_between_stops() -> None:
    """A leg's line runs from the point nearest one stop to the other."""
    shape = make_shape(
        shape_id='x',
        points=tuple((float(x), 0.0) for x in range(0, 1001, 100)),
    )
    line = leg_line(shape=shape, start=(200.0, 5.0), end=(700.0, -5.0))
    assert line == tuple((float(x), 0.0) for x in range(200, 701, 100))


def test_o2_centres_on_change() -> None:
    """O2 centres on the walk and opens one zoom finer."""
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert found.centre == (72.0, 0.0)
    assert found.zoom == 1
    assert found.departures == (
        SHAPED[0].first.departure,
        SHAPED[0].second.departure if SHAPED[0].second else None,
    )
    widest = connection_inputs(
        inputs=inputs_at(now=at(7, 35), zoom=0),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert widest.zoom == 0


def test_o2_shows_hero_pair_vehicles() -> None:
    """O2 shows the hero pair's leg-1 and leg-2 vehicles together."""
    framed = connection_inputs(
        inputs=inputs_at(now=at(7, 30)),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert framed.vehicle_trips == frozenset({'a0733', 'b0746'})


def test_o1_fits_whole_trip() -> None:
    """O1 picks the finest zoom that holds the whole trip."""
    box = ((0.0, 0.0), (3000.0, 2500.0))
    assert fit_zoom(points=box, width=520) == ZOOMS.index(8)
    far = ((0.0, 0.0), (100_000.0, 0.0))
    assert fit_zoom(points=far, width=520) == len(ZOOMS) - 1
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 35), zoom=0),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.WHOLE,
    )
    assert found.centre == (400.0, -596.0)
    assert found.zoom == ZOOMS.index(8)
    assert found.vehicle_trips == frozenset({
        'a0733', 'b0746', 'a0748', 'b0801a', 'a0803', 'b0817', 'a0822',
        'b0836',
    })


def test_connection_map_sidebar() -> None:
    """The map lists four pairs and marks the change and the To."""
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert len(view.pairs) == 4
    assert view.sidebar == ()
    assert view.pairs[0].detail == (
        '24 min · change at Constitution Hill, North West Twy'
    )
    assert view.pairs[1].detail == '24 min'
    trip = view.base.trip
    assert trip is not None
    assert trip.change == (72.0, 0.0)
    assert trip.walk == ((0.0, 0.0), (144.0, 0.0))
    assert trip.origin == (0.0, 900.0)
    assert trip.end == (800.0, -1900.0)
    assert view.base.route == (((0.0, 900.0), (0.0, 450.0), (0.0, 0.0)),)
    assert trip.second == (
        ((144.0, 0.0), (500.0, -900.0), (800.0, -1900.0)),
    )


def test_stay_pair_has_no_change() -> None:
    """A stay pair draws one line to the To and no walk."""
    stay = Pair(first=SHAPED[0].first, second=None)
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        pairs=(stay,),
        mode=ConnectionMap.CHANGES,
    )
    trip = view.base.trip
    assert trip is not None
    assert (trip.walk, trip.change, trip.second) == (None, None, ())
    assert view.pairs[0].detail.endswith(' min · stay on')


def test_no_shape_no_line() -> None:
    """Without a loaded shape no line is drawn."""
    view = build_connection_map(
        inputs=replace(inputs_at(now=at(7, 35)), shapes={}),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert view.base.route == ()
    assert view.base.trip is not None
    assert view.base.trip.second == ()


def test_vehicle_trips_filter_markers() -> None:
    """Only vehicles on the listed trips get markers."""
    feed: PositionsSnapshot = snapshot(
        fetched_at=at(7, 35),
        vehicles=(
            vehicle(timestamp=int(at(7, 35).timestamp()), trip_id='a0733'),
            vehicle(timestamp=int(at(7, 35).timestamp()), trip_id='other'),
        ),
    )
    inputs = replace(inputs_at(now=at(7, 35)), positions={'f': feed})
    view = build_connection_map(
        inputs=inputs, pin=WORK, pairs=SHAPED, mode=ConnectionMap.CHANGES,
    )
    assert len(view.markers) == 1


def test_no_pairs_keeps_framing() -> None:
    """With no pair, O2 still centres on the change and draws nothing."""
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        pairs=(),
        mode=ConnectionMap.CHANGES,
    )
    assert found.departures == ()
    assert found.vehicle_trips == frozenset()
    assert found.centre == (72.0, 0.0)
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        pairs=(),
        mode=ConnectionMap.CHANGES,
    )
    assert view.base.route == ()
    assert view.pairs == ()


def test_no_points_falls_back_to_from() -> None:
    """Without stop points, both framings keep the From and the knob."""
    bare = replace(
        WORK,
        origin=replace(WORK.origin, point=None),
        destination=None,
        ways=(Way(changes=(replace(
            WORK_CHANGE,
            alight=replace(WORK_CHANGE.alight, point=None),
            board=replace(WORK_CHANGE.board, point=None),
        ),)),),
    )
    for mode in ConnectionMap:
        found = connection_inputs(
            inputs=inputs_at(now=at(7, 35)), pin=bare, pairs=(), mode=mode,
        )
        assert found.centre == (0.0, 900.0)
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=bare,
        pairs=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert view.base.trip is not None
    assert view.base.trip.walk is None


def test_banner_keeps_frame_below_it() -> None:
    """Under a banner both framings move down by half its height."""
    stop = replace(STOP, banner='Timetable times · no live data yet')
    inputs = replace(inputs_at(now=at(7, 35)), stop_view=stop)
    change = connection_inputs(
        inputs=inputs, pin=WORK, pairs=SHAPED, mode=ConnectionMap.CHANGES,
    )
    assert change.centre == (72.0, BANNER_ROOM * ZOOMS[change.zoom] / 2)
    whole = connection_inputs(
        inputs=replace(inputs, zoom=0),
        pin=WORK,
        pairs=SHAPED,
        mode=ConnectionMap.WHOLE,
    )
    assert whole.zoom == ZOOMS.index(8)
    assert whole.centre == (400.0, -596.0 + BANNER_ROOM * 8 / 2)
