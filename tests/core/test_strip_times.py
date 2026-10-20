"""Tests for the trips that feed a strip."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.model import CarryOn, Departure, RouteKind
from paperroute.core.reach import StripKey
from paperroute.core.strip_times import board_departures, strip_trips
from tests.core.factories import START, departure

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
