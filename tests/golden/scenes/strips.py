"""Golden strip and connection scenes."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Final

from paperroute_testkit.city_setting import city_under_test, train_mode
from paperroute_testkit.core.factories import (
    EAST,
    EIGHT,
    POINTS,
    example_reach,
    line_pattern,
    pattern,
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
from paperroute_transit.core.geo import Point, Shape, make_shape
from paperroute_transit.core.messages import PatternsLoaded, StripLoaded
from paperroute_transit.core.model import Pin
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.patterns import PatternStop, TripPattern, measure
from paperroute_transit.core.reach import ReachInput, RunTail, build_reach
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


type Call = tuple[str, str, Point, int]  # station, name, metres, seconds

LOOP_441: Final[tuple[Call, ...]] = (
    ('204145', 'Mullens St Opp Goodsir St', (-2522.0, 784.0), 34560),
    ('203932', 'Robert St Before Victoria Rd', (-2571.0, 371.0), 34680),
    ('203956', 'White Bay, Victoria Rd', (-2535.0, 131.0), 34740),
    ('2000228', 'Sussex St After King St', (0.0, 0.0), 35160),
    ('200044', 'QVB, York St, Stand D', (245.0, -166.0), 35220),
    ('2000249', 'Town Hall, Park St, Stand H', (374.0, -361.0), 35280),
    ('2000157', 'Hyde Park, Park St, Stand B', (682.0, -395.0), 35400),
    (
        '2000144', 'Art Gallery Rd Opp Art Gallery Of NSW',
        (1307.0, 308.0), 35700,
    ),
    (
        '200097', 'Andrew (Boy) Charlton Pool, Mrs Macquaries Rd',
        (1605.0, 684.0), 35820,
    ),
    (
        '2000111', 'Art Gallery Of NSW, Art Gallery Rd',
        (1193.0, 134.0), 36120,
    ),
    ('200042', 'QVB, York St, Stand B', (252.0, -275.0), 36600),
    ('203933', 'Robert St At Crescent St', (-2603.0, 374.0), 36960),
    ('203934', 'Mullens St After Mansfield St', (-2517.0, 665.0), 37020),
)  # Sydney 441 towards Birchgrove, its From Sussex St After King St
SPUR_18: Final[tuple[Call, ...]] = (
    ('390020334', 'The Guineas Bus Station', (2563.0, 2099.0), 47700),
    ('390020182', 'Horse Racing Museum', (2482.0, 1829.0), 47727),
    ('390020192', 'White Lion', (2086.0, 1596.0), 47771),
    ('0500ESTET008', 'July Lodge', (0.0, 0.0), 48022),
    ('0500SLITW001', 'Tree Tops', (-4264.0, -4252.0), 48600),
    ('0500SLITW004', 'Rectory Farm Road', (-7313.0, -2661.0), 48851),
    ('0500SLITW006', 'High Street', (-7723.0, -2659.0), 48881),
    ('0500SLITW002', 'Manor Close', (-7974.0, -2671.0), 48900),
    ('0500SLITW005', 'High Street', (-7716.0, -2652.0), 48942),
    ('0500SLITW004', 'Rectory Farm Road', (-7313.0, -2661.0), 49009),
    ('0500SGREW003', 'Angle End', (-7138.0, -3321.0), 49122),
    ('0500SGREW005', 'Church Street', (-6951.0, -3750.0), 49200),
    ('0500SGREW004', 'Toft Lane', (-7484.0, -3878.0), 49248),
)  # Cambridge 18, its From July Lodge
SPUR_506: Final[tuple[Call, ...]] = (
    ('211252', 'Higginbotham Rd Before Cressy Rd', (-72.0, -782.0), 21720),
    ('211242', 'Quarry Rd At Badajoz Rd', (-138.0, -145.0), 21780),
    ('211243', 'Badajoz Rd Opp Callaghan St', (0.0, 0.0), 21840),
    ('211244', 'Badajoz Rd Before Nerang St', (120.0, 137.0), 21840),
    (
        'G211387', 'Twin Rd Opp Ryde East Public School',
        (344.0, 258.0), 21900,
    ),
    ('211382', 'Twin Rd Opp John Miller St', (498.0, 125.0), 21960),
    ('211383', 'Moncrieff Dr After Cressy Rd', (604.0, 36.0), 21960),
    ('211384', 'Moncrieff Dr Opp Cilento Cres', (736.0, -134.0), 22020),
    ('211385', 'Sager Place Centre, Sager Pl', (877.0, -181.0), 22020),
    ('211386', 'Elliott Av At Moncrieff Dr', (637.0, -1.0), 22080),
    ('211246', 'Twin Rd At John Miller St', (469.0, 130.0), 22140),
    ('G211387', 'Ryde East Public School, Twin Rd', (345.0, 238.0), 22140),
    ('2113116', 'Arndell School, Badajoz Rd', (345.0, 389.0), 22200),
    ('2113231', 'Macquarie Hospital, Badajoz Rd', (472.0, 539.0), 22200),
)  # Sydney 506 towards East Ryde, its From Badajoz Rd Opp Callaghan St;
# its two Ryde East Public School kerbs share their parent station
DETOUR_TRIP: Final[str] = 'detour'


def detour_scene(*, view: LiveView, trip: Sequence[Call], origin: int,
                 route: str) -> State:
    """
    Show one bus along real timetable calls on a Strip view.

    Parameters
    ----------
    view : LiveView
        The Strip or Strip + map.
    trip : Sequence[Call]
        The bus's calls, times in seconds into the day; a station
        called at twice is named for its first call.
    origin : int
        Index of the From among them.
    route : str
        The route's short name.

    Returns
    -------
    State
        State at `START`, the bus leaving the From two minutes later.
    """
    places = {
        station: place(station, name=name, point=point)
        for station, name, point, _ in reversed(trip)
    }
    pin = replace(PIN, origin=places[trip[origin][0]], destination=None)
    key = replace(KEY, station=trip[origin][0])
    stations = [station for station, _, _, _ in trip]
    shift = EIGHT + 120 - trip[origin][3]
    bus = replace(
        train(minutes=2, trip_id=DETOUR_TRIP), route=route,
    )
    state, _ = on_view(
        view=view, pin=pin, departures=(bus,), station=trip[origin][0],
    )
    state, _ = step(state, StripLoaded(key=key, reach=build_reach(
        data=ReachInput(
            origin=pin.origin,
            tails=(RunTail(
                before=tuple(stations[:origin]),
                after=tuple(dict.fromkeys(stations[origin + 1:])),
                trips=4,
            ),),
            places=places,
            station_of={station: station for station in stations},
        ),
    )))
    stops = tuple(
        PatternStop(
            arrives=seconds + shift,
            departs=seconds + shift,
            point=point,
            station=station,
            name=name,
        )
        for station, name, point, seconds in trip
    )
    return patterns_loaded(state=state, patterns={
        DETOUR_TRIP: pattern(trip_id=DETOUR_TRIP, route=route, stops=stops),
    })
