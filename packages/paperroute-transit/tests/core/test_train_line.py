"""Tests for a train Strip's main line."""

from datetime import timedelta

from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import (
    EIGHT,
    STALE_LIMIT,
    START,
    calls,
    departure,
    live,
    pattern,
)
from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.reach import Reach, ReachInput, RowKind
from paperroute_transit.core.strip_times import TripInputs
from paperroute_transit.core.train_line import (
    earlier,
    main_departure,
    train_reach,
)
from paperroute_transit.core.train_network import Hop, TrainNetwork, WeekdayRun


def trip_of(*, stations: tuple[str, ...], trip_id: str = 'a') -> TripPattern:
    """
    Make a pattern whose call at the From is at 8:10.

    Parameters
    ----------
    stations : tuple[str, ...]
        Station IDs in running order.
    trip_id : str
        Trip ID.

    Returns
    -------
    TripPattern
        The pattern; the first call at ``C`` leaves at 8:10.
    """
    start = EIGHT + 600 - 300 * stations.index('C')
    return pattern(
        trip_id=trip_id,
        stops=calls(stations=stations, start=start, step=300),
    )


def trips_of(
    *, patterns: dict[str, TripPattern | None], ids: tuple[str, ...],
) -> TripInputs:
    """
    Make inputs with one departure per trip ID at 8:10.

    Parameters
    ----------
    patterns : dict[str, TripPattern | None]
        Loaded patterns by trip ID.
    ids : tuple[str, ...]
        Trip IDs of the departures, in order.

    Returns
    -------
    TripInputs
        The inputs, at 8:00.
    """
    return TripInputs(
        departures=tuple(
            live(item=departure(
                departs=START + timedelta(minutes=10), trip_id=trip_id,
            ))
            for trip_id in ids
        ),
        key=None,
        patterns=patterns,
        positions={},
        now=START,
        stale_after=STALE_LIMIT,
        zone=city_under_test().zone,
        station_of={},
        line_kinds=frozenset(),
    )


def input_of(*, hops: dict[tuple[str, str], Hop]) -> ReachInput:
    """
    Make a Reach input for a From at C with a network of hops.

    Parameters
    ----------
    hops : dict[tuple[str, str], Hop]
        Hops by their two stations.

    Returns
    -------
    ReachInput
        The input, with no tails.
    """
    return ReachInput(
        origin=StopPlace(stop_id='C', name='C', point=None),
        tails=(),
        places={},
        station_of={},
        network=TrainNetwork(hops=hops, runs=()),
    )


def reach_of(
    *,
    stations: tuple[str, ...],
    hops: dict[tuple[str, str], Hop],
    destination: str | None = 'E',
    stops: tuple[PatternStop, ...] | None = None,
) -> Reach:
    """
    Lay out the main line of one trip.

    Parameters
    ----------
    stations : tuple[str, ...]
        Station IDs the trip calls at.
    hops : dict[tuple[str, str], Hop]
        Hops by their two stations.
    destination : str | None
        The Destination.
    stops : tuple[PatternStop, ...] | None
        Calls to use in place of ``stations``.

    Returns
    -------
    Reach
        The main line.
    """
    shaped = (
        trip_of(stations=stations)
        if stops is None
        else pattern(trip_id='a', stops=stops)
    )
    trips = trips_of(patterns={'a': shaped}, ids=('a',))
    return train_reach(
        data=input_of(hops=hops),
        trips=trips,
        main=trips.departures[0],
        destination=destination,
    )


def names(*, reach: Reach) -> list[str]:
    """
    List a Reach's station IDs.

    Parameters
    ----------
    reach : Reach
        The rows.

    Returns
    -------
    list[str]
        Station IDs, top to bottom.
    """
    return [row.place.stop_id for row in reach.rows]


CDEF = {('C', 'E'): Hop(passed=('D',), inside=True)}


def test_the_main_line_runs_to_the_end_of_the_trip() -> None:
    """The rows are the From, a passed station, then the later calls."""
    reach = reach_of(stations=('C', 'E', 'F'), hops=CDEF)
    assert [row.kind for row in reach.rows] == [
        RowKind.ORIGIN, RowKind.PASSED, RowKind.MAIN, RowKind.MAIN,
    ]
    assert names(reach=reach) == ['C', 'D', 'E', 'F']
    assert reach.trip == 'a'
    assert reach.day == START.date()


def test_earlier_calls_start_after_the_last_from_call() -> None:
    """A train through the From twice has earlier calls after the first."""
    shaped = pattern(trip_id='a', stops=calls(
        stations=('Z', 'C', 'Y', 'X', 'C', 'D'), start=EIGHT, step=300,
    ))
    found = earlier(pattern=shaped, call=4, origin='C')
    assert [stop.station for stop in found] == ['X', 'Y']


def test_earlier_calls_leave_out_stations_run_through() -> None:
    """A call with no pick-up or set-down is not an earlier call."""
    stops = calls(stations=('Y', 'X', 'C'), start=EIGHT, step=300)
    shaped = pattern(trip_id='a', stops=(
        stops[0], PatternStop(
            arrives=0, departs=0, point=None, station='X', passes=True,
        ), stops[2],
    ))
    found = earlier(pattern=shaped, call=2, origin='C')
    assert [stop.station for stop in found] == ['Y']


def test_the_followed_pass_is_the_listed_one() -> None:
    """With no Destination a loop train follows from its first call."""
    stations = ('Z', 'C', 'D', 'C', 'F')
    shaped = pattern(trip_id='a', stops=calls(
        stations=stations, start=EIGHT + 300, step=300,
    ))
    trips = trips_of(patterns={'a': shaped}, ids=('a',))
    reach = train_reach(
        data=input_of(hops={}),
        trips=trips,
        main=trips.departures[0],
        destination=None,
    )
    assert names(reach=reach) == ['Z', 'C', 'D']
    assert reach.rows[0].kind is RowKind.EARLIER


def test_no_station_is_listed_twice() -> None:
    """An earlier call at a main-line or Destination station is dropped."""
    reach = reach_of(
        stations=('E', 'Y', 'C', 'D', 'E', 'F'),
        hops={},
        destination='Y',
    )
    assert names(reach=reach) == ['C', 'D', 'E', 'F']
    assert len(set(names(reach=reach))) == len(reach.rows)


def test_earlier_rows_read_upward_from_the_from() -> None:
    """The nearest earlier call is just above the From."""
    reach = reach_of(stations=('Y', 'X', 'C', 'E'), hops={})
    assert names(reach=reach) == ['Y', 'X', 'C', 'E']
    assert [row.parent for row in reach.rows] == [1, 2, None, 2]
    assert [row.above for row in reach.rows] == [True, True, False, False]


def test_a_train_starting_at_the_from_has_nothing_above() -> None:
    """No earlier rows when the first call is at the From."""
    reach = reach_of(stations=('C', 'E'), hops={})
    assert names(reach=reach) == ['C', 'E']
    assert reach.rows[0].kind is RowKind.ORIGIN


def test_a_hop_outside_the_track_has_no_passed_rows() -> None:
    """The row the hop arrives at is marked outside."""
    reach = reach_of(
        stations=('C', 'E', 'F'),
        hops={
            ('C', 'E'): Hop(passed=(), inside=False),
            ('E', 'F'): Hop(passed=(), inside=True),
        },
    )
    assert [row.kind for row in reach.rows] == [
        RowKind.ORIGIN, RowKind.MAIN, RowKind.MAIN,
    ]
    assert [row.inside for row in reach.rows] == [True, False, True]


def test_a_destination_that_is_passed_is_a_passed_row() -> None:
    """A Destination the train runs through is listed as passed."""
    reach = reach_of(stations=('C', 'E', 'F'), hops=CDEF, destination='D')
    assert reach.rows[1].kind is RowKind.PASSED
    assert names(reach=reach) == ['C', 'D', 'E', 'F']


def test_a_stop_run_through_is_not_a_main_row() -> None:
    """A call with no pick-up or set-down is left out of the line."""
    stops = calls(stations=('C', 'D', 'E'), start=EIGHT + 600, step=300)
    reach = reach_of(
        stations=(),
        hops={},
        stops=(stops[0], PatternStop(
            arrives=stops[1].arrives, departs=stops[1].departs,
            point=None, station='D', passes=True,
        ), stops[2]),
    )
    assert names(reach=reach) == ['C', 'E']


def test_no_main_gives_the_from_alone() -> None:
    """With no train followed the Reach is the From."""
    trips = trips_of(patterns={}, ids=())
    reach = train_reach(
        data=input_of(hops={}), trips=trips, main=None, destination='E',
    )
    assert names(reach=reach) == ['C']
    assert reach.trip is None
    assert reach.day is None


def test_a_cancelled_or_unloaded_main_falls_to_the_next() -> None:
    """The first live item with a pattern is the main departure."""
    ok = trip_of(stations=('C', 'E'), trip_id='ok')
    trips = trips_of(
        patterns={'cancelled': trip_of(stations=('C', 'E')), 'gone': None,
                  'ok': ok},
        ids=('cancelled', 'gone', 'ok'),
    )
    items: list[LiveDeparture] = [
        live(item=trips.departures[0].departure, cancelled=True),
        trips.departures[1],
        trips.departures[2],
    ]
    found = main_departure(
        items=items, trips=trips, origin='C', picked=None,
    )
    assert found is items[2]
    assert main_departure(
        items=[], trips=trips, origin='C', picked=None,
    ) is None


def test_a_picked_item_is_returned_as_it_is() -> None:
    """A picked cancelled item is the main departure."""
    trips = trips_of(
        patterns={'a': trip_of(stations=('C', 'E'))}, ids=('a', 'b'),
    )
    items = [live(item=trips.departures[0].departure, cancelled=True)]
    found = main_departure(
        items=items, trips=trips, origin='C', picked=0,
    )
    assert found is items[0]
    assert main_departure(
        items=items, trips=trips, origin='C', picked=5,
    ) is None


def test_a_repeated_call_still_moves_the_next_hop_on() -> None:
    """A hop is looked up from the previous call, listed or not."""
    reach = reach_of(
        stations=('C', 'D', 'C', 'F'),
        hops={
            ('C', 'D'): Hop(passed=(), inside=True),
            ('D', 'C'): Hop(passed=(), inside=True),
            ('C', 'F'): Hop(passed=('X',), inside=True),
        },
        destination='F',
    )
    assert names(reach=reach) == ['C', 'D', 'X', 'F']
    assert [row.inside for row in reach.rows] == [True] * 4


def test_a_call_at_a_passed_station_moves_the_next_hop_on() -> None:
    """After a call at a station already passed, the hop starts there."""
    reach = reach_of(
        stations=('C', 'E', 'D', 'F'),
        hops={
            ('C', 'E'): Hop(passed=('D',), inside=True),
            ('D', 'F'): Hop(passed=('Y',), inside=True),
        },
        destination=None,
    )
    assert names(reach=reach) == ['C', 'D', 'E', 'Y', 'F']
    assert reach.rows[1].kind is RowKind.PASSED
    assert reach.rows[-1].inside


def two_ways(
    *,
    below: tuple[str, ...],
    above: tuple[str, ...],
    hops: dict[tuple[str, str], Hop] | None = None,
) -> Reach:
    """
    Lay out a Strip with a train each way and no Destination.

    Parameters
    ----------
    below : tuple[str, ...]
        Stations of the train below, calling at C.
    above : tuple[str, ...]
        Stations of the train above, calling at C.
    hops : dict[tuple[str, str], Hop] | None
        Hops by their two stations.

    Returns
    -------
    Reach
        The Reach with both trains.
    """
    trips = trips_of(
        patterns={
            'a': trip_of(stations=below, trip_id='a'),
            'b': trip_of(stations=above, trip_id='b'),
        },
        ids=('a', 'b'),
    )
    return train_reach(
        data=input_of(hops=hops or {}),
        trips=trips,
        main=trips.departures[0],
        destination=None,
        above=trips.departures[1],
    )


def test_a_train_each_way_reads_up_and_down_from_the_from() -> None:
    """The above train's rows run upward with the From in the middle."""
    reach = two_ways(below=('C', 'E', 'F'), above=('C', 'N', 'M'))
    assert names(reach=reach) == ['M', 'N', 'C', 'E', 'F']
    assert [row.above for row in reach.rows] == [
        True, True, False, False, False,
    ]
    assert [row.parent for row in reach.rows] == [1, 2, None, 2, 3]
    assert reach.trip == 'a'
    assert reach.stations == 2


def test_the_above_train_keeps_hops_from_a_repeated_call() -> None:
    """A hop is looked up from the previous call even when not listed."""
    reach = two_ways(
        below=('C', 'E'),
        above=('C', 'N', 'E', 'M'),
        hops={
            ('C', 'N'): Hop(passed=(), inside=True),
            ('N', 'E'): Hop(passed=(), inside=True),
            ('C', 'E'): Hop(passed=(), inside=True),
            ('E', 'M'): Hop(passed=('P',), inside=False),
        },
    )
    assert names(reach=reach) == ['M', 'P', 'N', 'C', 'E']
    assert [row.inside for row in reach.rows] == [
        False, True, True, True, True,
    ]


def test_a_train_calling_at_the_from_again_is_cut() -> None:
    """Neither train lists a station after it calls at the From again."""
    reach = two_ways(
        below=('C', 'E', 'C', 'F'), above=('C', 'N', 'C', 'M'),
    )
    assert names(reach=reach) == ['N', 'C', 'E']


def test_a_station_both_trains_reach_is_listed_once_below() -> None:
    """Two trains round a loop share a station: only the below one lists it."""
    reach = two_ways(below=('C', 'E', 'L'), above=('C', 'N', 'L', 'M'))
    assert names(reach=reach) == ['M', 'N', 'C', 'E', 'L']
    assert reach.rows[-1].above is False
    assert len(set(names(reach=reach))) == len(reach.rows)


def test_an_above_train_gives_no_earlier_rows() -> None:
    """With a train each way the earlier calls are left out."""
    reach = two_ways(below=('Y', 'C', 'E'), above=('X', 'C', 'N'))
    assert names(reach=reach) == ['N', 'C', 'E']


def test_with_no_main_the_above_train_is_still_drawn() -> None:
    """A side with no train leaves its half of the Strip empty."""
    trips = trips_of(
        patterns={'b': trip_of(stations=('C', 'N'), trip_id='b')},
        ids=('b',),
    )
    reach = train_reach(
        data=input_of(hops={}),
        trips=trips,
        main=None,
        destination=None,
        above=trips.departures[0],
    )
    assert names(reach=reach) == ['N', 'C']
    assert reach.trip == 'b'


def exits_reach(
    *,
    runs: dict[str, int],
    below: tuple[str, ...] = ('C', 'E', 'F'),
    above: tuple[str, ...] | None = None,
    hops: dict[tuple[str, str], Hop] | None = None,
    destination: str | None = None,
) -> Reach:
    """
    Lay out a Strip whose network has weekday runs.

    Parameters
    ----------
    runs : dict[str, int]
        Weekday trains by space-separated stations, each through C.
    below : tuple[str, ...]
        Stations of the train below, calling at C.
    above : tuple[str, ...] | None
        Stations of the train above; None for no train above.
    hops : dict[tuple[str, str], Hop] | None
        Hops by their two stations.
    destination : str | None
        The Destination.

    Returns
    -------
    Reach
        The Reach; C is at the origin, N1 and N2 lie north, E south.
    """
    patterns: dict[str, TripPattern | None] = {
        'a': trip_of(stations=below, trip_id='a'),
    }
    if above is not None:
        patterns['b'] = trip_of(stations=above, trip_id='b')
    trips = trips_of(patterns=patterns, ids=tuple(patterns))
    points = {
        'C': (0, 0),
        'N1': (0, 900),
        'E': (0, -900),
        'Z': (0, -1800),
    }
    data = ReachInput(
        origin=StopPlace(stop_id='C', name='C', point=(0, 0)),
        tails=(),
        places={
            name: StopPlace(stop_id=name, name=name.lower(), point=point)
            for name, point in points.items()
        },
        station_of={},
        network=TrainNetwork(
            hops=hops or {},
            runs=tuple(
                WeekdayRun(
                    route='X', stations=tuple(stations.split()), trains=count,
                )
                for stations, count in runs.items()
            ),
        ),
    )
    return train_reach(
        data=data,
        trips=trips,
        main=trips.departures[0],
        destination=destination,
        above=trips.departures[1] if above is not None else None,
        loop_name='City Circle',
    )


MAIN_RUN = {'C E F': 40}


def test_an_exit_row_follows_its_main_junction_row() -> None:
    """The exit hangs after E, and the row after it keeps its parent."""
    reach = exits_reach(runs={**MAIN_RUN, 'C E Z': 8})
    assert names(reach=reach) == ['C', 'E', 'Z', 'F']
    exit_row = reach.rows[2]
    assert exit_row.kind is RowKind.EXIT
    assert (exit_row.parent, exit_row.trips, exit_row.names) == (1, 8, ('z',))
    assert (exit_row.depth, exit_row.above, exit_row.lines) == (
        0, False, (),
    )
    assert reach.rows[3].parent == 1
    assert reach.stations == 2


def test_an_exit_row_follows_a_passed_junction_row() -> None:
    """A junction that is passed takes its exit after the passed row."""
    reach = exits_reach(
        runs={'C E F': 40, 'C G H': 9},
        hops={
            ('C', 'E'): Hop(passed=('D',), inside=True),
            ('C', 'G'): Hop(passed=('D',), inside=True),
        },
    )
    assert names(reach=reach) == ['C', 'D', 'G', 'E', 'F']
    assert reach.rows[1].kind is RowKind.PASSED
    assert reach.rows[2].kind is RowKind.EXIT
    assert [row.parent for row in reach.rows] == [None, 0, 1, 1, 3]


def test_an_exit_at_the_from_hangs_just_below_it() -> None:
    """With the From as junction the exit is the first row below it."""
    reach = exits_reach(runs={**MAIN_RUN, 'C Z': 8})
    assert names(reach=reach) == ['C', 'Z', 'E', 'F']
    assert reach.rows[1].kind is RowKind.EXIT
    assert [row.parent for row in reach.rows] == [None, 0, 0, 2]


def test_exits_at_one_junction_go_busiest_first() -> None:
    """The exit with more trains is the nearer row."""
    reach = exits_reach(runs={**MAIN_RUN, 'C E Y': 7, 'C E Z': 9})
    assert names(reach=reach) == ['C', 'E', 'Z', 'Y', 'F']
    assert [row.parent for row in reach.rows[2:4]] == [1, 1]


def test_exits_before_and_beyond_the_destination_are_kept() -> None:
    """Rows after the Destination and before it both carry exits."""
    reach = exits_reach(
        runs={**MAIN_RUN, 'C E Y': 7, 'C E F Z': 9},
        destination='E',
    )
    assert names(reach=reach) == ['C', 'E', 'Y', 'F', 'Z']


def test_the_above_train_has_exits_after_its_junction() -> None:
    """An exit above hangs outward of its junction, parented to it."""
    reach = exits_reach(
        runs={**MAIN_RUN, 'C N1 N2': 40, 'C N1 W': 8, 'C E Z': 8},
        above=('C', 'N1', 'N2'),
    )
    assert names(reach=reach) == ['N2', 'W', 'N1', 'C', 'E', 'Z', 'F']
    assert [row.parent for row in reach.rows] == [2, 2, 3, None, 3, 4, 4]
    assert [row.above for row in reach.rows] == [
        True, True, True, False, False, False, False,
    ]
    assert reach.rows[1].kind is RowKind.EXIT


def test_a_circle_exit_is_named_by_the_city() -> None:
    """A tail that comes back to the From carries the loop's name."""
    reach = exits_reach(runs={**MAIN_RUN, 'C E T C': 12})
    assert reach.rows[2].names == ('City Circle',)


def test_a_network_with_no_runs_adds_no_exit_rows() -> None:
    """Rows are unchanged when no run leaves the line."""
    reach = exits_reach(runs=MAIN_RUN)
    assert [row.kind for row in reach.rows] == [
        RowKind.ORIGIN, RowKind.MAIN, RowKind.MAIN,
    ]


def test_a_terminus_pin_cuts_a_train_at_its_return() -> None:
    """With no Destination and no train above the line stops at the From."""
    reach = reach_of(
        stations=('C', 'E', 'C', 'F'), hops={}, destination=None,
    )
    assert names(reach=reach) == ['C', 'E']


def test_a_destination_beyond_the_return_is_not_cut() -> None:
    """With a Destination the train's whole route is listed."""
    reach = reach_of(
        stations=('C', 'E', 'C', 'F'), hops={}, destination='F',
    )
    assert names(reach=reach) == ['C', 'E', 'F']
