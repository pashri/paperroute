"""Tests for the connection Map's model."""

from dataclasses import replace
from datetime import datetime
from types import MappingProxyType
from typing import Final

from paperroute.core.chains import Chain, Legs, leg_key
from paperroute.core.connection_map import (
    BANNER_ROOM,
    build_connection_map,
    change_panes,
    connection_inputs,
    fit_zoom,
    leg_line,
)
from paperroute.core.connection_model import way_legs
from paperroute.core.geo import Shape, make_shape
from paperroute.core.map_view import (
    DEFAULT_ZOOM,
    NO_LOCATION,
    MapInputs,
    MapView,
)
from paperroute.core.messages import DeparturesLoaded, LoadShapes
from paperroute.core.model import Change, Departure, Pin, StopPlace, Way
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.saved import ConnectionMap, LiveView
from paperroute.core.state import step
from paperroute.core.state_views import current_view, live_stop_view
from paperroute.core.stop_view import empty_text
from paperroute.core.tiles import ZOOMS
from tests.core.connection_factories import (
    HOLLIS,
    LEG1,
    LEG2,
    PARRAMATTA,
    WESTMEAD,
    WORK,
    WORK_CHANGE,
    at,
    run,
)
from tests.core.factories import live, place, snapshot, vehicle
from tests.core.test_connection_screens import started
from tests.core.test_connection_strip import CHAINS, STOP

LEG_ONE: Final[Shape] = make_shape(
    shape_id='s606', points=((0.0, 900.0), (0.0, 450.0), (0.0, 0.0)),
)
LEG_TWO: Final[Shape] = make_shape(
    shape_id='s665',
    points=((144.0, 0.0), (500.0, -900.0), (800.0, -1900.0)),
)


def shaped(*, chains: tuple[Chain, ...]) -> tuple[Chain, ...]:
    """
    Give each chain's legs the test shapes.

    Parameters
    ----------
    chains : tuple[Chain, ...]
        Chains.

    Returns
    -------
    tuple[Chain, ...]
        The chains with shape IDs set by leg.
    """
    return tuple(
        replace(chain, legs=tuple(
            replace(
                item,
                departure=replace(item.departure, shape_id=shape_id),
            )
            for item, shape_id in zip(chain.legs, ('s606', 's665'))
        ))
        for chain in chains
    )


SHAPED: Final[tuple[Chain, ...]] = shaped(chains=CHAINS)


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
        chains=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert found.centre == (72.0, 0.0)
    assert found.zoom == 1
    assert found.departures == tuple(
        item.departure for item in SHAPED[0].legs
    )
    widest = connection_inputs(
        inputs=inputs_at(now=at(7, 35), zoom=0),
        pin=WORK,
        chains=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert widest.zoom == 0


def test_o2_shows_hero_pair_vehicles() -> None:
    """O2 shows the hero pair's leg-1 and leg-2 vehicles together."""
    framed = connection_inputs(
        inputs=inputs_at(now=at(7, 30)),
        pin=WORK,
        chains=SHAPED,
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
        chains=SHAPED,
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
        chains=SHAPED,
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
    stay = Chain(legs=SHAPED[0].legs[:1], way=None)
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        chains=(stay,),
        mode=ConnectionMap.CHANGES,
    )
    trip = view.base.trip
    assert trip is not None
    assert (trip.walk, trip.change, trip.second) == (None, None, ())
    assert view.pairs[0].detail.endswith(' min · direct')


def test_no_shape_no_line() -> None:
    """Without a loaded shape no line is drawn."""
    view = build_connection_map(
        inputs=replace(inputs_at(now=at(7, 35)), shapes={}),
        pin=WORK,
        chains=SHAPED,
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
        inputs=inputs, pin=WORK, chains=SHAPED, mode=ConnectionMap.CHANGES,
    )
    assert len(view.markers) == 1


def test_no_pairs_keeps_framing() -> None:
    """With no pair, O2 still centres on the change and draws nothing."""
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        chains=(),
        mode=ConnectionMap.CHANGES,
    )
    assert found.departures == ()
    assert found.vehicle_trips == frozenset()
    assert found.centre == (72.0, 0.0)
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=WORK,
        chains=(),
        mode=ConnectionMap.CHANGES,
    )
    assert view.base.route == ()
    assert view.pairs == ()
    assert view.empty == 'No connections to Westmead Station in the next 3 hours'


def test_chains_hide_the_direct_list_notice() -> None:
    """The direct list's empty text is not shown over listed chains."""
    direct = empty_text(destination='Westmead Station', loaded=True)
    inputs = replace(
        inputs_at(now=at(7, 35)), stop_view=replace(STOP, empty=direct),
    )
    view = build_connection_map(
        inputs=inputs, pin=WORK, chains=SHAPED, mode=ConnectionMap.CHANGES,
    )
    assert view.empty is None
    two = replace(two_inputs(), stop_view=replace(STOP, empty=direct))
    panes = change_panes(
        inputs=two, pin=TWO, chain=TWO_CHAIN, mode=ConnectionMap.CHANGES,
    )
    assert [pane.empty for pane in panes] == [None, None]


def test_no_location_still_said() -> None:
    """A From with no location keeps its own notice."""
    view = build_connection_map(
        inputs=replace(inputs_at(now=at(7, 35)), centre=None),
        pin=replace(WORK, origin=replace(WORK.origin, point=None)),
        chains=(),
        mode=ConnectionMap.START,
    )
    assert view.empty == NO_LOCATION


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
            inputs=inputs_at(now=at(7, 35)), pin=bare, chains=(), mode=mode,
        )
        assert found.centre == (0.0, 900.0)
    view = build_connection_map(
        inputs=inputs_at(now=at(7, 35)),
        pin=bare,
        chains=SHAPED,
        mode=ConnectionMap.CHANGES,
    )
    assert view.base.trip is not None
    assert view.base.trip.walk is None


def test_banner_keeps_frame_below_it() -> None:
    """Under a banner both framings move down by half its height."""
    stop = replace(STOP, banner='Timetable times · no live data yet')
    inputs = replace(inputs_at(now=at(7, 35)), stop_view=stop)
    change = connection_inputs(
        inputs=inputs, pin=WORK, chains=SHAPED, mode=ConnectionMap.CHANGES,
    )
    assert change.centre == (72.0, BANNER_ROOM * ZOOMS[change.zoom] / 2)
    whole = connection_inputs(
        inputs=replace(inputs, zoom=0),
        pin=WORK,
        chains=SHAPED,
        mode=ConnectionMap.WHOLE,
    )
    assert whole.zoom == ZOOMS.index(8)
    assert whole.centre == (400.0, -596.0 + BANNER_ROOM * 8 / 2)


PLATFORM: Final[StopPlace] = place(
    '2145W', name='Westmead Station Platform 2', point=(820.0, -1900.0),
)
TWO: Final[Pin] = Pin(
    origin=HOLLIS,
    destination=PARRAMATTA,
    kind=None,
    ways=(Way(changes=(
        WORK_CHANGE, Change(alight=WESTMEAD, board=PLATFORM, allow=4),
    )),),
)
LEG_THREE: Final[Shape] = make_shape(
    shape_id='st1', points=((820.0, -1900.0), (2000.0, -2500.0)),
)
T1: Final[Departure] = replace(
    run(departs=(8, 5), arrives=(8, 20), route='T1', trip_id='c0805'),
    shape_id='st1',
)
TWO_CHAIN: Final[Chain] = Chain(
    legs=(*SHAPED[0].legs, live(item=T1)), way=0,
)


def two_inputs(*, zoom: int = DEFAULT_ZOOM) -> MapInputs:
    """
    Map inputs for the two-change pin, with all three shapes.

    Parameters
    ----------
    zoom : int
        Knob zoom.

    Returns
    -------
    MapInputs
        The inputs.
    """
    inputs = inputs_at(now=at(7, 35), zoom=zoom)
    return replace(inputs, shapes={**inputs.shapes, 'st1': LEG_THREE})


def test_changes_two_panes_side_by_side() -> None:
    """Two changes give two half-width panes, one on each walk."""
    panes = change_panes(
        inputs=two_inputs(), pin=TWO, chain=TWO_CHAIN,
        mode=ConnectionMap.CHANGES,
    )
    assert [pane.base.width for pane in panes] == [400, 400]
    assert [pane.base.centre for pane in panes] == [
        (72.0, 0.0), (810.0, -1900.0),
    ]
    assert {pane.zoom for pane in panes} == {DEFAULT_ZOOM - 1}
    first, second = (pane.base.trip for pane in panes)
    assert first is not None and second is not None
    assert first.walk == ((0.0, 0.0), (144.0, 0.0))
    assert second.walk == ((800.0, -1900.0), (820.0, -1900.0))
    assert second.second == (((820.0, -1900.0), (2000.0, -2500.0)),)


def test_changes_one_change_one_pane() -> None:
    """One change gives one pane of the map's own width."""
    (pane,) = change_panes(
        inputs=inputs_at(now=at(7, 35)), pin=WORK, chain=SHAPED[0],
        mode=ConnectionMap.CHANGES,
    )
    assert pane.base.width == inputs_at(now=at(7, 35)).width
    assert pane.base.centre == (72.0, 0.0)


def test_whole_fits_all_legs() -> None:
    """The whole trip draws every leg, alternating solid and dashed."""
    (pane,) = change_panes(
        inputs=two_inputs(zoom=0), pin=TWO, chain=TWO_CHAIN,
        mode=ConnectionMap.WHOLE,
    )
    trip = pane.base.trip
    assert trip is not None
    assert pane.base.route == (
        ((0.0, 900.0), (0.0, 450.0), (0.0, 0.0)),
        ((820.0, -1900.0), (2000.0, -2500.0)),
    )
    assert trip.second == (
        ((144.0, 0.0), (500.0, -900.0), (800.0, -1900.0)),
    )
    assert trip.others == ((((800.0, -1900.0), (820.0, -1900.0))),)
    assert pane.base.centre is not None
    assert pane.base.centre[0] == 1000.0
    assert ZOOMS[pane.zoom] >= 8


def test_shapes_requested_for_each_leg() -> None:
    """The Map asks for the shape of every leg of a two-change chain."""
    state = started(view=LiveView.MAP, pins=(TWO,))
    one, two, three = way_legs(pin=TWO, way=TWO.ways[0])
    after, effects = step(state, DeparturesLoaded(
        pin=TWO,
        departures=(),
        until=at(10, 30),
        legs=Legs(lists=MappingProxyType({
            leg_key(leg=one): tuple(
                replace(item, shape_id='s606') for item in LEG1
            ),
            leg_key(leg=two): tuple(
                replace(item, shape_id='s665') for item in LEG2
            ),
            leg_key(leg=three): (T1,),
        })),
    ))
    [load] = [item for item in effects if isinstance(item, LoadShapes)]
    assert load.shape_ids == frozenset({'s606', 's665', 'st1'})
    view = current_view(state=after, stop_view=live_stop_view(state=after))
    assert isinstance(view, MapView)
    assert len(view.panes) == 1
    assert view.base.width + view.panes[0].base.width == 800


def test_first_leg_fits_first_leg() -> None:
    """The first-leg setting fits the first leg and shows its vehicle."""
    whole = connection_inputs(
        inputs=inputs_at(now=at(7, 30), zoom=0),
        pin=WORK,
        chains=SHAPED,
        mode=ConnectionMap.WHOLE,
    )
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 30), zoom=0),
        pin=WORK,
        chains=SHAPED,
        mode=ConnectionMap.FIRST_LEG,
    )
    assert found.centre is not None
    assert found.centre[0] == 0.0
    assert found.zoom < whole.zoom
    assert found.vehicle_trips == frozenset({'a0733'})


def test_first_leg_direct_is_whole_trip() -> None:
    """A direct row's first leg is the whole trip."""
    direct = (Chain(legs=SHAPED[0].legs[:1], way=None),)
    first, whole = (
        connection_inputs(
            inputs=inputs_at(now=at(7, 30), zoom=0),
            pin=WORK,
            chains=direct,
            mode=mode,
        )
        for mode in (ConnectionMap.FIRST_LEG, ConnectionMap.WHOLE)
    )
    assert (first.centre, first.zoom) == (whole.centre, whole.zoom)


def test_start_centres_on_origin() -> None:
    """The start setting zooms on the From with first-leg vehicles."""
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 30)),
        pin=WORK,
        chains=SHAPED,
        mode=ConnectionMap.START,
    )
    assert found.centre == (0.0, 900.0)
    assert found.zoom == 0
    assert found.vehicle_trips == frozenset(
        {'a0733', 'a0748', 'a0803', 'a0822'},
    )


def test_start_is_finest_zoom_and_never_coarser_than_first_leg() -> None:
    """The start setting uses the finest zoom, at any knob setting."""
    for zoom in range(len(ZOOMS)):
        start, first = (
            connection_inputs(
                inputs=inputs_at(now=at(7, 30), zoom=zoom),
                pin=WORK,
                chains=SHAPED,
                mode=mode,
            )
            for mode in (ConnectionMap.START, ConnectionMap.FIRST_LEG)
        )
        assert start.zoom == 0
        assert start.zoom <= first.zoom


def test_new_settings_one_pane() -> None:
    """The first-leg and start settings give one full-width pane."""
    for mode in (ConnectionMap.FIRST_LEG, ConnectionMap.START):
        (pane,) = change_panes(
            inputs=two_inputs(), pin=TWO, chain=TWO_CHAIN, mode=mode,
        )
        assert pane.base.width == two_inputs().width


def test_first_leg_without_chains_keeps_from() -> None:
    """With no chain the first-leg setting stays on the From."""
    found = connection_inputs(
        inputs=inputs_at(now=at(7, 30)),
        pin=WORK,
        chains=(),
        mode=ConnectionMap.FIRST_LEG,
    )
    assert (found.centre, found.zoom) == ((0.0, 900.0), DEFAULT_ZOOM)
