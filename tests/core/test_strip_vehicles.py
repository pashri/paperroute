"""Tests for the trips that feed a strip."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.map_view import Marker
from paperroute.core.model import Departure, RouteKind
from paperroute.core.reach import StripKey
from paperroute.core.strip_vehicles import (
    BADGE_STEP,
    Spot,
    pushed,
    strip_trips,
)
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


def badge(*, x: float, y: float, route: str = 'T1') -> Marker:
    """
    Make a live strip badge.

    Parameters
    ----------
    x : float
        Track x.
    y : float
        Centre y.
    route : str
        Route label.

    Returns
    -------
    Marker
        The badge.
    """
    return Marker(
        route=route, x=x, y=y, bearing=180.0, live=True, pinned=True,
    )


def test_pushed_caps_a_crowded_hop_at_two_badges() -> None:
    """Eight badges on one hop show one badge and a '+7' just below it."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=136)}
    crowd = [badge(x=30, y=100 + i, route=f'T{i}') for i in range(8)]
    first, rest = pushed(markers=crowd, spots=spots)
    assert (first.route, first.y, first.live) == ('T0', 100, True)
    assert (rest.route, rest.y, rest.live) == ('+7', 100 + BADGE_STEP, False)
    assert (rest.bearing, rest.age) == (None, None)


def test_pushed_keeps_badges_apart_in_separate_hops() -> None:
    """Badges far enough apart are each kept, pushed at most one step."""
    spots = {'A': Spot(x=30, y=100), 'B': Spot(x=30, y=172)}
    shown = pushed(
        markers=[badge(x=30, y=y) for y in (100, 110, 172, 172)],
        spots=spots,
    )
    assert [each.y for each in shown] == [100, 122, 172, 194]
    assert {each.route for each in shown} == {'T1'}


def test_pushed_keeps_spur_badges_off_main_line_names() -> None:
    """A spur badge level with a main-line row moves onto its track."""
    spots = {
        'main': Spot(x=30, y=100),
        'spur': Spot(x=46, y=136),
    }
    junction, spur = pushed(
        markers=[badge(x=46, y=100), badge(x=46, y=136)], spots=spots,
    )
    assert (junction.x, junction.y) == (30, 100)
    assert (spur.x, spur.y) == (46, 136)
    (between,) = pushed(markers=[badge(x=46, y=118)], spots=spots)
    assert between.x == 46
