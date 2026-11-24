"""Tests for a train Strip's times and carry-on note."""

from dataclasses import replace
from datetime import timedelta

from paperroute_testkit.core.factories import START, departure, live
from paperroute_transit.core.model import CarryOn, StopPlace
from paperroute_transit.core.reach import Reach, ReachInput, ReachRow, RowKind
from paperroute_transit.core.strip_times import TripInputs
from paperroute_transit.core.train_network import TrainNetwork
from paperroute_transit.core.train_times import (
    is_train,
    train_note,
    train_times,
)
from tests.core.builders import trip_inputs

HERE = StopPlace(stop_id='C', name='C', point=None)
SOURCE = ReachInput(
    origin=HERE,
    tails=(),
    places={},
    station_of={},
    network=TrainNetwork(hops={}, runs=()),
)


def reach_of(*, trip: str | None) -> Reach:
    """
    Make a train reach of the From alone, following a trip.

    Parameters
    ----------
    trip : str | None
        The followed trip ID, or None.

    Returns
    -------
    Reach
        The reach, with a source.
    """
    return Reach(
        rows=(
            ReachRow(
                place=HERE, kind=RowKind.ORIGIN, depth=0, above=False,
                parent=None,
            ),
        ),
        stations=0,
        station_of={},
        trip=trip,
        day=None if trip is None else START.date(),
        source=SOURCE,
    )


def trips_of(*, then: CarryOn | None) -> TripInputs:
    """
    Make inputs with one departure of trip ``a`` and no patterns.

    Parameters
    ----------
    then : CarryOn | None
        The service the train runs next.

    Returns
    -------
    TripInputs
        The inputs, at 8:00.
    """
    return trip_inputs(
        departures=(live(item=departure(
            departs=START, trip_id='a', then=then,
        )),),
        patterns={},
    )


def test_a_reach_with_a_trip_and_a_source_is_a_train_reach() -> None:
    """Only a source and a followed trip make a train reach."""
    assert is_train(reach=reach_of(trip='a'))
    assert not is_train(reach=reach_of(trip=None))
    assert not is_train(reach=replace(reach_of(trip='a'), source=None))


def test_a_trip_that_is_gone_has_no_times() -> None:
    """With no departure of the followed trip, no row has a time."""
    trips = trips_of(then=None)
    assert train_times(trips=trips, reach=reach_of(trip='gone')) == {}
    assert train_times(trips=trips, reach=reach_of(trip='a')) == {0: '8:00'}


def test_a_loop_train_times_the_pass_it_follows() -> None:
    """Two passes of one trip at the From give the followed pass's time."""
    first = departure(departs=START, trip_id='a')
    second = replace(
        departure(departs=START + timedelta(minutes=40), trip_id='a'),
        origin_seq=9,
    )
    trips = replace(
        trips_of(then=None),
        departures=(live(item=first), live(item=second)),
    )
    followed = replace(reach_of(trip='a'), seq=9)
    assert train_times(trips=trips, reach=followed) == {0: '8:40'}
    assert train_times(trips=trips, reach=reach_of(trip='a')) == {0: '8:00'}


def test_a_note_needs_a_trip_that_carries_on_from_a_loaded_pattern() -> None:
    """With no loaded pattern there is no station to put the note on."""
    carry = CarryOn(route='T2', headsign='Leppington', trip_id='b')
    reach = reach_of(trip='a')
    assert train_note(trips=trips_of(then=None), reach=reach) is None
    assert train_note(trips=trips_of(then=carry), reach=reach) is None
    assert train_note(
        trips=trips_of(then=carry), reach=reach_of(trip='gone'),
    ) is None
