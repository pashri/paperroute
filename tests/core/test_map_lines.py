"""Tests for the Map's route lines."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    calls,
    departure,
    pattern,
    place,
)
from paperroute_transit.core.geo import ALONG, Point, Shape, make_shape
from paperroute_transit.core.map_line import MapLine
from paperroute_transit.core.map_lines import (
    RING_DISTANCE,
    LineChoice,
    first_per_line,
    map_line_key,
    plain_lines,
    plain_map_view,
    ride_span,
    ring_points,
    ringable,
)
from paperroute_transit.core.map_view import MapInputs
from paperroute_transit.core.model import Departure, StopPlace, TripFacts
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.tiles import ZOOMS
from tests.core.test_map_fit import plain, stop_view


def rail(*, route: str, route_id: str) -> Departure:
    """A train departure on a feed route."""
    return replace(
        departure(departs=START, route=route),
        facts=TripFacts(route_id=route_id, road=False),
    )


def test_map_line_key_is_the_route_on_the_road() -> None:
    """A bus's line is its route."""
    assert map_line_key(departure=departure(departs=START)) == '606'


def test_map_line_key_is_the_feed_route_on_rail() -> None:
    """Two trips on one feed route share a line; another route does not."""
    norwich = rail(route='LE', route_id='rail:LE:Norwich')
    again = rail(route='LE', route_id='rail:LE:Norwich')
    ipswich = rail(route='LE', route_id='rail:LE:Ipswich')
    assert map_line_key(departure=norwich) == map_line_key(departure=again)
    assert map_line_key(departure=norwich) != map_line_key(
        departure=ipswich,
    )


def test_first_per_line_keeps_order() -> None:
    """The first departure of each line is kept, in order."""
    a = departure(departs=START, route='606', trip_id='a')
    b = departure(departs=START, route='610', trip_id='b')
    c = departure(departs=START, route='606', trip_id='c')
    assert first_per_line(departures=(a, b, c)) == (a, b)


EAST = make_shape(shape_id='east', points=((0.0, 0.0), (5000.0, 0.0)))
NORTH = make_shape(shape_id='north', points=((0.0, 0.0), (0.0, 5000.0)))
WEST = make_shape(shape_id='west', points=((0.0, 0.0), (-5000.0, 0.0)))
LOOP = make_shape(
    shape_id='loop',
    points=(
        (0.0, 0.0), (900.0, 0.0), (900.0, 900.0), (0.0, 900.0),
        (0.0, 1800.0),
    ),
)
SHAPES: Final[dict[str, Shape]] = {
    shape.shape_id: shape for shape in (EAST, NORTH, WEST, LOOP)
}
FROM_POINT: Final[Point] = (1000.0, 0.0)
TO_POINT: Final[Point] = (3000.0, 0.0)
STOPS: Final[dict[str, Point]] = {
    'W': (0.0, 0.0), 'P': FROM_POINT, 'N': (2000.0, 0.0), 'T': TO_POINT,
    'Z': (4000.0, 0.0), 'Q': (-2000.0, 0.0),
}
CHOICE: Final[LineChoice] = LineChoice(station='P')


def bus(
    *, route: str, shape: str, minutes: int = 0, kind: str | None = None,
) -> Departure:
    """A bus on a shape, leaving the From `minutes` after eight."""
    return replace(
        departure(
            departs=START + timedelta(minutes=minutes), ride=10,
            route=route, trip_id=f'{route}-{minutes}', kind=kind,
        ),
        shape_id=shape,
    )


def east_pattern(*, trip_id: str) -> TripPattern:
    """W, P, N, T, Z along EAST, 5 minutes apart, leaving P at eight."""
    return replace(
        pattern(
            trip_id=trip_id, shape_id='east',
            stops=calls(
                stations='WPNTZ', start=EIGHT - 300, step=300, points=STOPS,
            ),
        ),
        along=(0.0, 1000.0, 2000.0, 3000.0, 4000.0),
    )


def north_pattern(*, trip_id: str) -> TripPattern:
    """P and two stops north of it along NORTH."""
    return replace(
        pattern(
            trip_id=trip_id, shape_id='north',
            stops=calls(
                stations='PAB', start=EIGHT, step=300,
                points={'P': FROM_POINT, 'A': (0.0, 1000.0),
                        'B': (0.0, 2000.0)},
            ),
        ),
        along=(0.0, 1000.0, 2000.0),
    )


def lines_inputs(
    *, departures: tuple[Departure, ...], destination: bool = False,
    patterns: dict[str, TripPattern | None] | None = None,
) -> MapInputs:
    """A plain pin at FROM_POINT at 25 m/px over the four shapes."""
    return replace(
        plain(
            departures=departures, patterns=patterns,
            ends=(FROM_POINT, TO_POINT if destination else None),
        ),
        stop_view=replace(
            stop_view(), destination='T' if destination else None,
        ),
        zoom=ZOOMS.index(25),
        shapes=SHAPES,
    )


def test_drawn_five_are_solid_from_the_from_to_the_end() -> None:
    """Each listed route is one line, solid from the From onward."""
    first = bus(route='606', shape='east')
    head = (first, bus(route='606', shape='east', minutes=10),
            bus(route='610', shape='north', minutes=5))
    lines = plain_lines(
        inputs=lines_inputs(
            departures=head,
            patterns={first.trip_id: east_pattern(trip_id=first.trip_id)},
        ),
        choice=CHOICE,
    )
    assert [(line.shape_id, line.solid) for line in lines] == [
        ('east', (1000.0, 5000.0)), ('north', (0.0, 5000.0)),
    ]


def test_other_routes_of_the_day_are_dashed_end_to_end() -> None:
    """A route serving the stop today but not drawn is dashed whole."""
    lines = plain_lines(
        inputs=lines_inputs(departures=(bus(route='606', shape='east'),)),
        choice=replace(CHOICE, extras=(
            bus(route='620', shape='west', minutes=90),
            bus(route='606', shape='north', minutes=30),
        )),
    )
    assert [(line.shape_id, line.solid) for line in lines][1:] == [
        ('west', None),
    ]


def test_hidden_school_runs_get_no_line() -> None:
    """With school buses off, no school run is drawn, listed or not."""
    lines = plain_lines(
        inputs=lines_inputs(departures=(
            bus(route='606', shape='east'),
            bus(route='S1', shape='north', kind='school'),
        )),
        choice=replace(
            CHOICE,
            extras=(bus(route='S2', shape='west', kind='school'),),
            hidden=frozenset({'school'}),
        ),
    )
    assert [line.shape_id for line in lines] == ['east']


def test_destination_draws_every_departure_solid_between_the_ends() -> None:
    """With a Destination every loaded shape is solid From to To."""
    first = bus(route='606', shape='east')
    lines = plain_lines(
        inputs=lines_inputs(
            departures=(first, bus(route='610', shape='north')),
            destination=True,
            patterns={first.trip_id: east_pattern(trip_id=first.trip_id)},
        ),
        choice=replace(CHOICE, extras=(bus(route='620', shape='west'),)),
    )
    assert [line.shape_id for line in lines] == ['east', 'north']
    assert lines[0].solid == (1000.0, 3000.0)


def test_ride_span_uses_the_call_the_departure_boards_at() -> None:
    """On a loop that calls at the From twice, the boarding call counts."""
    loop = replace(
        pattern(
            trip_id='loop', shape_id='loop',
            stops=calls(
                stations='PXYP', start=EIGHT, step=600,
                points={'P': (0.0, 0.0), 'X': (900.0, 0.0),
                        'Y': (900.0, 900.0)},
            ),
        ),
        along=(0.0, 900.0, 1800.0, 2700.0),
    )
    assert ride_span(
        departure=bus(route='606', shape='loop', minutes=30),
        pattern=loop, shape=LOOP, station='P',
        ends=((0.0, 0.0), None), zone=SYDNEY_ZONE,
    ) == (2700.0, 3600.0)


def test_ride_starts_at_a_stand_under_a_station() -> None:
    """Calls name parent 'P'; the board's station finds the From call."""
    ride = bus(route='606', shape='east')
    parented = east_pattern(trip_id=ride.trip_id)
    assert ride_span(
        departure=ride, pattern=parented, shape=EAST, station='P',
        ends=(FROM_POINT, None), zone=SYDNEY_ZONE,
    ) == (1000.0, 5000.0)
    off_point = (400.0, 0.0)
    assert ride_span(
        departure=ride, pattern=parented, shape=EAST, station='P',
        ends=(off_point, None), zone=SYDNEY_ZONE,
    ) == (1000.0, 5000.0)
    assert ride_span(
        departure=ride, pattern=parented, shape=EAST, station='F1',
        ends=(off_point, None), zone=SYDNEY_ZONE,
    ) == (400.0, 5000.0)


def test_ride_span_falls_back_to_the_nearest_point() -> None:
    """Without a pattern the span starts at the shape's point nearest."""
    ride = bus(route='606', shape='east')
    assert ride_span(
        departure=ride, pattern=None, shape=EAST, station='P',
        ends=((250.0, 3.0), None), zone=SYDNEY_ZONE,
    ) == (250.0, 5000.0)
    assert ride_span(
        departure=ride, pattern=None, shape=EAST, station='P',
        ends=(None, None), zone=SYDNEY_ZONE,
    ) is None


RIDE = bus(route='606', shape='east')
OTHER = bus(route='620', shape='west')
RIDE_LINE = MapLine(shape_id='east', solid=(1000.0, 3000.0), shape=EAST)
OTHER_LINE = MapLine(shape_id='west', solid=None, shape=WEST)
OTHER_PATTERN = replace(
    pattern(
        trip_id=OTHER.trip_id, shape_id='west',
        stops=calls(stations='PQ', start=EIGHT, step=300, points=STOPS),
    ),
    along=(0.0, 3000.0),
)


def rings_of(*, ride: TripPattern) -> tuple[tuple[Point, ...], ...]:
    """Rings of RIDE on `ride` and OTHER, with a Destination."""
    return ring_points(
        inputs=lines_inputs(
            departures=(RIDE,), destination=True,
            patterns={RIDE.trip_id: ride, OTHER.trip_id: OTHER_PATTERN},
        ),
        lines=(RIDE_LINE, OTHER_LINE),
        departures=(RIDE, OTHER),
    )


def test_rings_take_their_line_part_and_solid_wins() -> None:
    """Calls outside the ride ring dashed, inside solid, shared solid."""
    solid, dashed = rings_of(ride=east_pattern(trip_id=RIDE.trip_id))
    assert solid == (FROM_POINT, (2000.0, 0.0), TO_POINT)
    assert dashed == ((0.0, 0.0), (4000.0, 0.0), (-2000.0, 0.0))


def test_passed_stations_get_no_ring() -> None:
    """A call the train passes is not ringed."""
    ride = east_pattern(trip_id=RIDE.trip_id)
    stops = list(ride.stops)
    stops[2] = replace(stops[2], passes=True)
    solid, dashed = rings_of(ride=replace(ride, stops=tuple(stops)))
    assert (2000.0, 0.0) not in solid + dashed


def test_rings_off_the_map_are_left_out() -> None:
    """A call beyond the window plus 20 px is not carried."""
    ride = east_pattern(trip_id=RIDE.trip_id)
    far = (FROM_POINT[0] + 300 * 25.0, 0.0)
    stops = (*ride.stops[:-1], replace(ride.stops[-1], point=far))
    solid, dashed = rings_of(ride=replace(ride, stops=stops))
    assert far not in solid + dashed


def test_plain_map_view_carries_lines_and_rings() -> None:
    """The view's base holds the lines and both ring sets, no route."""
    inputs = lines_inputs(
        departures=(RIDE,), destination=True,
        patterns={RIDE.trip_id: east_pattern(trip_id=RIDE.trip_id)},
    )
    base = plain_map_view(inputs=inputs, choice=CHOICE).base
    assert base.route == ()
    assert base.lines == plain_lines(inputs=inputs, choice=CHOICE)
    assert (base.stops, base.dashed_stops) == ring_points(
        inputs=inputs, lines=base.lines, departures=(RIDE,),
    )


def test_a_hidden_school_run_keeps_its_vehicle() -> None:
    """A listed school run loses its line and rings, not its vehicle."""
    school = bus(route='S1', shape='east', kind='school')
    inputs = lines_inputs(
        departures=(school,),
        patterns={school.trip_id: east_pattern(trip_id=school.trip_id)},
    )
    hidden = replace(CHOICE, hidden=frozenset({'school'}))
    view = plain_map_view(inputs=inputs, choice=hidden)
    assert not view.base.lines
    assert view.base.stops == view.base.dashed_stops == ()
    assert view.markers
    assert view.markers == plain_map_view(
        inputs=inputs, choice=CHOICE,
    ).markers


def test_a_second_variant_of_a_drawn_line_gets_no_rings() -> None:
    """With no Destination only the drawn shape's stops are ringed."""
    east = bus(route='606', shape='east')
    north = bus(route='606', shape='north', minutes=30)
    inputs = lines_inputs(
        departures=(east, north),
        patterns={
            east.trip_id: east_pattern(trip_id=east.trip_id),
            north.trip_id: north_pattern(trip_id=north.trip_id),
        },
    )
    view = plain_map_view(inputs=inputs, choice=CHOICE)
    assert [line.shape_id for line in view.base.lines] == ['east']
    rung = view.base.stops + view.base.dashed_stops
    assert (0.0, 1000.0) not in rung
    assert (0.0, 2000.0) not in rung
    assert FROM_POINT in rung


def test_an_extra_dropped_for_a_drawn_key_gets_no_rings() -> None:
    """An extra of an already-drawn line adds no line and no rings."""
    east = bus(route='606', shape='east')
    extra = bus(route='606', shape='north', minutes=30)
    inputs = lines_inputs(
        departures=(east,),
        patterns={
            east.trip_id: east_pattern(trip_id=east.trip_id),
            extra.trip_id: north_pattern(trip_id=extra.trip_id),
        },
    )
    view = plain_map_view(
        inputs=inputs, choice=replace(CHOICE, extras=(extra,)),
    )
    assert [line.shape_id for line in view.base.lines] == ['east']
    rung = view.base.stops + view.base.dashed_stops
    assert (0.0, 1000.0) not in rung


def test_ride_ends_at_the_destination_call_among_equal_arrivals() -> None:
    """Two calls share the arrival minute; the Destination's is taken."""
    ride = bus(route='606', shape='east')
    shared = replace(
        pattern(
            trip_id=ride.trip_id, shape_id='east',
            stops=calls(
                stations='PNTZ', start=EIGHT, step=300, points=STOPS,
            ),
        ),
        along=(1000.0, 2000.0, 3000.0, 4000.0),
    )
    stops = list(shared.stops)
    stops[1] = replace(stops[1], arrives=stops[2].arrives)
    shared = replace(shared, stops=tuple(stops))
    arrives = shared.stops[2].arrives
    assert arrives == EIGHT + 600
    assert ride_span(
        departure=ride, pattern=shared, shape=EAST, station='P',
        ends=(FROM_POINT, TO_POINT), zone=SYDNEY_ZONE,
    ) == (1000.0, 3000.0)
    assert ride_span(
        departure=ride, pattern=shared, shape=EAST, station='P',
        ends=(FROM_POINT, (2000.0, 0.0)), zone=SYDNEY_ZONE,
    ) == (1000.0, 2000.0)


def test_departures_on_one_drawn_shape_draw_one_line() -> None:
    """Different shape ids that resolve to one pattern shape draw once."""
    first = bus(route='606', shape='a')
    second = bus(route='606', shape='b', minutes=30)
    lines = plain_lines(
        inputs=lines_inputs(
            departures=(first, second),
            destination=True,
            patterns={
                first.trip_id: east_pattern(trip_id=first.trip_id),
                second.trip_id: east_pattern(trip_id=second.trip_id),
            },
        ),
        choice=CHOICE,
    )
    assert [line.shape_id for line in lines] == ['east']


def test_departures_with_no_shape_id_draw_their_own_shapes() -> None:
    """Departures with no shape id still draw each pattern's shape."""
    first = replace(bus(route='606', shape='x'), shape_id=None)
    second = replace(
        bus(route='610', shape='x', minutes=30), shape_id=None,
    )
    lines = plain_lines(
        inputs=lines_inputs(
            departures=(first, second),
            destination=True,
            patterns={
                first.trip_id: east_pattern(trip_id=first.trip_id),
                second.trip_id: north_pattern(trip_id=second.trip_id),
            },
        ),
        choice=CHOICE,
    )
    assert [line.shape_id for line in lines] == ['east', 'north']


def ringed_with(
    *, index: int, point: Point,
) -> tuple[tuple[Point, ...], ...]:
    """Rings of RIDE with its call `index` moved to `point`."""
    base = east_pattern(trip_id=RIDE.trip_id)
    stops = list(base.stops)
    stops[index] = replace(stops[index], point=point)
    return rings_of(ride=replace(base, stops=tuple(stops)))


def test_a_call_near_the_line_is_ringed() -> None:
    """A call 100 m from the shape is ringed, solid or dashed."""
    solid, _ = ringed_with(index=3, point=(3000.0, 100.0))
    _, dashed = ringed_with(index=4, point=(4000.0, 100.0))
    assert (3000.0, 100.0) in solid
    assert (4000.0, 100.0) in dashed


def test_a_call_far_from_the_line_is_not_ringed() -> None:
    """A call 200 m from the shape gets no ring, solid or dashed."""
    for index, point in ((3, (3000.0, 200.0)), (4, (4000.0, 200.0))):
        assert point not in sum(ringed_with(index=index, point=point), ())


def test_a_call_beyond_a_truncated_shape_is_not_ringed() -> None:
    """A call farther than the ring distance past the shape's end gets no ring."""
    near, far = (5100.0, 0.0), (5200.0, 0.0)
    assert near in sum(ringed_with(index=4, point=near), ())
    assert far not in sum(ringed_with(index=4, point=far), ())


def test_a_call_off_the_map_is_not_measured_against_the_shape() -> None:
    """A call off the map adds no `nearest_along` cache miss."""
    inputs = lines_inputs(departures=(RIDE,))
    far = (FROM_POINT[0] + 300 * 25.0, 0.0)
    ALONG.clear()
    assert not ringable(
        inputs=inputs, centre=FROM_POINT, shape=EAST, point=far,
    )
    assert ALONG.misses == 0


def test_a_repeat_build_at_a_busy_stop_measures_nothing_again() -> None:
    """Rings built twice over 300 candidates miss the cache once each."""
    names = [f's{index}' for index in range(300)]
    points = {
        name: (FROM_POINT[0] + 5.0 * index, 0.0)
        for index, name in enumerate(names)
    }
    busy = replace(
        pattern(
            trip_id=RIDE.trip_id, shape_id='east',
            stops=calls(stations=names, points=points),
        ),
        along=tuple(1000.0 + 5.0 * index for index in range(300)),
    )

    def build() -> int:
        ring_points(
            inputs=lines_inputs(
                departures=(RIDE,), patterns={RIDE.trip_id: busy},
            ),
            lines=(RIDE_LINE,),
            departures=(RIDE,),
        )
        return ALONG.misses

    ALONG.clear()
    first = build()
    assert first == 300
    assert build() == first


def test_rings_past_the_destination_are_dashed() -> None:
    """Calls after the Destination ring dashed, never solid."""
    solid, dashed = rings_of(ride=east_pattern(trip_id=RIDE.trip_id))
    assert (4000.0, 0.0) in dashed
    assert all(point[0] <= TO_POINT[0] for point in solid)


def test_a_call_is_ringed_up_to_the_ring_distance() -> None:
    """A call just inside RING_DISTANCE is ringed; just outside is not."""
    assert RING_DISTANCE == 151.0
    inside = (3000.0, RING_DISTANCE - 0.1)
    outside = (3000.0, RING_DISTANCE + 0.1)
    assert inside in ringed_with(index=3, point=inside)[0]
    assert outside not in sum(ringed_with(index=3, point=outside), ())


def test_a_line_over_moved_geometry_is_not_equal() -> None:
    """The same id over a longer shape compares unequal."""
    longer = make_shape(
        shape_id='east', points=((0.0, 0.0), (6000.0, 0.0)),
    )
    moved = MapLine(shape_id='east', solid=RIDE_LINE.solid, shape=longer)
    assert moved != RIDE_LINE
    same = MapLine(shape_id='east', solid=RIDE_LINE.solid, shape=EAST)
    assert same == RIDE_LINE


BACK = make_shape(shape_id='back', points=((5000.0, 30.0), (0.0, 30.0)))
ACROSS_KERB: Final[StopPlace] = place(
    'X', name='Opposite', point=(1100.0, 30.0),
)


def across(found: Departure) -> Departure:
    """The same departure from the kerb across the road."""
    return replace(found, facts=replace(found.facts, across=True))


def test_plain_map_view_joins_the_kerb_across_to_the_from() -> None:
    """A both-sides pin's From marks carry the kerb across."""
    inputs = lines_inputs(departures=(RIDE,))
    base = plain_map_view(
        inputs=inputs, choice=replace(CHOICE, across=ACROSS_KERB),
    ).base
    assert base.trip is not None
    assert base.trip.across == ACROSS_KERB.point
    plain_base = plain_map_view(inputs=inputs, choice=CHOICE).base
    assert plain_base.trip is not None and plain_base.trip.across is None
