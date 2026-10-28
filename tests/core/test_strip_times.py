"""Tests for the trips that feed a strip."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.model import CarryOn, Departure, RouteKind
from paperroute.core.reach import StripKey
from paperroute.core.strip_times import (
    STRIP_HOPS,
    TripInputs,
    approach_times,
    arrivals_after,
    board_departures,
    call_delay,
    filtered,
    joined_ids,
    strip_trips,
    trip_ids,
)
from tests.core.factories import EIGHT, START, calls, departure, live, pattern

TRAIN: Final[StripKey] = StripKey(
    station='C', kind=RouteKind.TRAIN, routes=(),
)


def train(*, minutes: int, trip_id: str) -> Departure:
    """
    Make a train departure some minutes after 8:00.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00.
    trip_id : str
        Trip ID.

    Returns
    -------
    Departure
        The departure.
    """
    return departure(
        departs=START + timedelta(minutes=minutes),
        trip_id=trip_id,
        route='T1',
        kind=RouteKind.TRAIN,
    )


def test_strip_trips_keep_every_trip_in_the_window() -> None:
    """Every trip within the hour before and half hour after is kept."""
    now = START + timedelta(hours=2)
    kept = [
        replace(
            train(minutes=0, trip_id=f'k{i}'),
            departs=now - timedelta(minutes=60) + timedelta(seconds=36 * i),
        )
        for i in range(150)
    ]
    dropped = [
        replace(
            train(minutes=0, trip_id=trip_id),
            departs=now + timedelta(minutes=offset),
        )
        for trip_id, offset in (('early', -61), ('late', 31))
    ]
    assert strip_trips(
        departures=[*kept, *dropped], key=TRAIN, now=now,
    ) == {f'k{i}' for i in range(150)}


def test_strip_trips_follow_the_routes_rule() -> None:
    """A bus key takes its routes, or every bus route when it has none."""
    ours = departure(departs=START, trip_id='a', kind=RouteKind.BUS)
    theirs = departure(
        departs=START, trip_id='b', route='609', kind=RouteKind.BUS,
    )
    chosen = StripKey(station='C', kind=RouteKind.BUS, routes=('606',))
    every = StripKey(station='C', kind=RouteKind.BUS, routes=())
    anything = StripKey(station='C', kind=None, routes=())
    both = [ours, theirs]
    assert strip_trips(departures=both, key=chosen, now=START) == {'a'}
    assert strip_trips(departures=both, key=every, now=START) == {'a', 'b'}
    assert strip_trips(departures=both, key=anything, now=START) == {
        'a', 'b',
    }


def test_strip_trips_include_next_trips() -> None:
    """A T8 that carries on as trip b brings b's trip ID along."""
    t8 = departure(
        departs=START,
        trip_id='a',
        route='T8',
        kind=RouteKind.TRAIN,
        then=CarryOn(route='T2', headsign='Leppington', trip_id='b'),
    )
    assert strip_trips(departures=[t8], key=TRAIN, now=START) == {'a', 'b'}


def test_bus_board_keeps_school_and_replacement() -> None:
    """A Bus board keeps school and replacement buses; others their kind."""
    kinds = (
        RouteKind.BUS, RouteKind.SCHOOL, RouteKind.REPLACEMENT,
        RouteKind.TRAIN, RouteKind.COACH, None,
    )
    every = [
        departure(departs=START, trip_id=str(kind), kind=kind)
        for kind in kinds
    ]
    boards = {
        kind: [
            item.trip_id
            for item in board_departures(departures=every, kind=kind)
        ]
        for kind in kinds
    }
    assert boards[RouteKind.BUS] == ['bus', 'school', 'replacement']
    assert boards[RouteKind.SCHOOL] == ['school']
    assert boards[RouteKind.TRAIN] == ['train']
    assert boards[None] == [str(kind) for kind in kinds]


def approach_inputs(*, late: int = 0) -> TripInputs:
    """
    Make trips through C at 8:10 and 8:20 from A; the first skips B.

    Parameters
    ----------
    late : int
        Minutes late for each departure.

    Returns
    -------
    TripInputs
        The trips, at 8:00.
    """
    first = train(minutes=10, trip_id='a')
    second = train(minutes=20, trip_id='b')
    return TripInputs(
        departures=(
            live(item=second, late=late), live(item=first, late=late),
        ),
        key=TRAIN,
        patterns={
            'a': pattern(trip_id='a', stops=calls(
                stations=('A', 'C'), start=EIGHT + 300, step=300,
            )),
            'b': pattern(trip_id='b', stops=calls(
                stations=('A', 'B', 'C'), start=EIGHT, step=600,
            )),
        },
        positions={},
        now=START,
        station_of={},
    )


def test_approach_times_follow_the_next_departure() -> None:
    """The earlier trip's calls give the times, shifted by its delay."""
    on_time = approach_times(
        trips=approach_inputs(), origin='C', stations={'A', 'B'},
    )
    assert on_time == {'A': START + timedelta(minutes=5)}
    late = approach_times(
        trips=approach_inputs(late=2), origin='C', stations={'A'},
    )
    assert late == {'A': START + timedelta(minutes=7)}


def test_approach_time_blank_where_trip_skips() -> None:
    """A station the next trip runs through gets no time."""
    assert 'B' not in approach_times(
        trips=approach_inputs(), origin='C', stations={'A', 'B'},
    )


def test_approach_time_uses_a_departure_at_second_zero() -> None:
    """A departure at service-second 0 is used, not the arrival."""
    plain = approach_inputs()
    midnight = calls(stations=('A', 'C'), start=0, step=300)
    early = (replace(midnight[0], arrives=-60), midnight[1])
    trips = replace(
        plain,
        patterns={
            **plain.patterns,
            'a': pattern(trip_id='a', stops=early),
        },
    )
    expected = replace(
        plain,
        patterns={
            **plain.patterns,
            'a': pattern(trip_id='a', stops=midnight),
        },
    )
    assert approach_times(
        trips=trips, origin='C', stations={'A'},
    ) == approach_times(trips=expected, origin='C', stations={'A'})


def test_trip_ids_include_the_trip_run_on_through_as() -> None:
    """A carry-on departure asks for the trip it becomes."""
    carried = replace(
        train(minutes=1, trip_id='a'),
        facts=replace(
            train(minutes=1, trip_id='a').facts,
            onto=CarryOn(route='T2', headsign='W', trip_id='b'),
        ),
    )
    assert trip_ids(departures=[carried]) == {'a', 'b'}


def test_joined_ids_follow_loaded_patterns_up_to_the_hop_limit() -> None:
    """Joins are followed while patterns are loaded, at most STRIP_HOPS."""
    patterns = {
        f'j{k}': replace(pattern(trip_id=f'j{k}'), next_trip=f'j{k + 1}')
        for k in range(STRIP_HOPS + 3)
    }
    assert joined_ids(starts={'j0'}, patterns=patterns) == {
        f'j{k}' for k in range(1, STRIP_HOPS + 1)
    }
    assert joined_ids(starts={'j0'}, patterns={'j0': patterns['j0']}) == {
        'j1',
    }


def test_approach_times_skip_a_station_run_through() -> None:
    """A station the next train runs through has no approach time."""
    plain = approach_inputs()
    through = calls(stations=('A', 'B', 'C'), start=EIGHT + 300, step=150)
    trips = replace(plain, patterns={
        **plain.patterns,
        'a': pattern(trip_id='a', stops=(
            through[0], replace(through[1], passes=True), through[2],
        )),
    })
    assert set(approach_times(
        trips=trips, origin='C', stations={'A', 'B'},
    )) == {'A'}


def test_calls_after_leave_out_stations_run_through() -> None:
    """Times after the From skip a call with no pick-up or set-down."""
    plain = approach_inputs()
    stops = calls(stations=('C', 'D', 'E'), start=EIGHT + 600, step=300)
    trips = replace(plain, patterns={
        'a': pattern(trip_id='a', stops=(
            stops[0], replace(stops[1], passes=True), stops[2],
        )),
    })
    item = live(item=train(minutes=10, trip_id='a'))
    assert [
        station for station, _ in arrivals_after(
            item=item, trips=trips, origin='C',
        )
    ] == ['E']


def test_filtered_keeps_only_routes() -> None:
    """Only the chosen routes stay, in order; no choice keeps all."""
    found = [
        departure(
            departs=START + timedelta(minutes=minutes),
            trip_id=route,
            route=route,
        )
        for minutes, route in enumerate(('606', '607X', '610'))
    ]
    kept = filtered(departures=found, only=('606', '607X'))
    assert [d.route for d in kept] == ['606', '607X']
    assert filtered(departures=found, only=()) == tuple(found)


def test_arrivals_after_meet_the_live_arrival_at_the_destination() -> None:
    """Calls move from the From's delay to the live arrival's delay."""
    plain = approach_inputs()
    trip = replace(
        train(minutes=10, trip_id='a'),
        arrives=START + timedelta(minutes=20),
    )
    item = replace(
        live(item=trip, late=2),
        arrives=trip.arrives - timedelta(minutes=1),
    )
    trips = replace(plain, patterns={
        'a': pattern(trip_id='a', stops=calls(
            stations=('C', 'D', 'E', 'F'), start=EIGHT + 600, step=300,
        )),
    })
    assert dict(arrivals_after(item=item, trips=trips, origin='C')) == {
        'D': START + timedelta(minutes=15, seconds=30),
        'E': item.arrives,
        'F': START + timedelta(minutes=24),
    }


def test_call_delay_keeps_the_from_delay_without_a_later_arrival() -> None:
    """An arrival time not after the From leaves the From's delay."""
    ends = ((EIGHT + 60, 120), (EIGHT + 30, 0))
    assert call_delay(seconds=EIGHT, ends=ends) == 120
