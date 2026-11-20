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
from paperroute_transit.core.train_network import Hop, TrainNetwork


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
    assert names(reach=reach) == ['Z', 'C', 'D', 'F']
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
        destination=None,
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
