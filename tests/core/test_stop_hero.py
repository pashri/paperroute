"""Tests for the gauge the Stop view gains from its hero's trip."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_ZONE, SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    departure,
    pattern,
    place,
    snapshot,
    vehicle,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import on_view
from paperroute_testkit.render.factories import stop_row
from paperroute_transit.core.messages import PatternsLoaded
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.core.stop_hero import with_detail
from paperroute_transit.core.stop_under import GaugeCall, UnderNumber
from paperroute_transit.core.stop_view import StopView

STAND: Final[str] = '2145615'
FROM: Final[str] = 'G2145615'
HERO: Final[Departure] = departure(
    departs=START + timedelta(minutes=3),
    mode=SydneyFeed.BUS,
    kind=SydneyMode.BUS,
    platform='B',
)


def stop(
    *, at: int, x: float, station: str, name: str, passes: bool = False,
) -> PatternStop:
    """
    A call `at` seconds from eight o'clock, `x` metres east.

    Parameters
    ----------
    at : int
        Seconds after 8:00.
    x : float
        Metres east of the origin.
    station : str
        Station ID.
    name : str
        Station name.
    passes : bool
        Whether the trip runs through.

    Returns
    -------
    PatternStop
        The call.
    """
    return PatternStop(
        arrives=EIGHT + at,
        departs=EIGHT + at,
        point=(x, 0.0),
        station=station,
        name=name,
        passes=passes,
    )


RUN: Final[TripPattern] = pattern(
    trip_id='trip-0803',
    stops=(
        stop(at=-540, x=-450.0, station='A', name='Westmead'),
        stop(at=-420, x=-300.0, station='P', name='Junction', passes=True),
        stop(at=-180, x=-150.0, station='B', name='Mays Hill'),
        stop(at=180, x=0.0, station=FROM, name='Hollis St'),
        stop(at=360, x=150.0, station='D', name='Harris Park'),
        stop(at=420, x=200.0, station='E', name='Harris Park'),
        stop(at=540, x=300.0, station='F', name='Rosehill'),
        stop(at=720, x=450.0, station='G', name='Parramatta Station'),
    ),
)
CALLS: Final[tuple[GaugeCall, ...]] = (
    GaugeCall(name='Harris Park', minutes=':08'),
    GaugeCall(name='Rosehill', minutes=':11'),
    GaugeCall(name='Parramatta', minutes=':14'),
)
ON_TIME: Final[tuple[GaugeCall, ...]] = (
    GaugeCall(name='Harris Park', minutes=':06'),
    GaugeCall(name='Rosehill', minutes=':09'),
    GaugeCall(name='Parramatta', minutes=':12'),
)


def open_view(*, hero: Departure = HERO) -> StopView:
    """
    A Stop view with no Destination, its block under the number empty.

    Parameters
    ----------
    hero : Departure
        The departure behind the big number.

    Returns
    -------
    StopView
        The view, 2 minutes late.
    """
    return StopView(
        origin='Hollis St',
        destination=None,
        clock='8:00',
        hero=stop_row(),
        rows=(),
        banner=None,
        empty=None,
        scroll=0,
        head_departures=(hero,),
        under=UnderNumber(status='2 min late'),
        delay=120,
    )


def seen_at(
    *,
    x: float,
    trip_id: str = 'trip-0803',
    heard: datetime = START,
    fetched: datetime = START,
) -> dict[str, PositionsSnapshot]:
    """
    One positions download holding a vehicle.

    Parameters
    ----------
    x : float
        Metres east of the origin.
    trip_id : str
        The vehicle's trip.
    heard : datetime
        When the vehicle reported.
    fetched : datetime
        When the feed was downloaded.

    Returns
    -------
    dict[str, PositionsSnapshot]
        The download by feed.
    """
    report = vehicle(
        timestamp=int(heard.timestamp()), trip_id=trip_id, point=(x, 0.0),
    )
    return {'buses': snapshot(fetched_at=fetched, vehicles=(report,))}


def detail(**changes: object) -> StopView:
    """
    Add the detail to the open view, with any input replaced.

    Parameters
    ----------
    **changes : object
        Inputs of `with_detail` to replace.

    Returns
    -------
    StopView
        The result.
    """
    inputs: dict[str, object] = {
        'view': open_view(),
        'station': FROM,
        'patterns': {'trip-0803': RUN},
        'positions': seen_at(x=-75.0),
        'now': START,
        'limit': timedelta(minutes=2),
        'zone': SYDNEY_ZONE,
        **changes,
    }
    return with_detail(**inputs)  # type: ignore[arg-type]


def test_detail_fills_the_gauge_and_the_words() -> None:
    """A fresh position on the hero's trip gives the gauge and its words."""
    view = detail()
    assert view.under is not None
    assert view.under.place == '1 stop away'
    assert view.gauge is not None
    assert view.gauge.behind == ('Mays Hill',)
    assert view.gauge.calls == CALLS


def test_a_vehicle_on_the_trip_before_gives_no_place() -> None:
    """Another trip's vehicle gives no place; the calls are still filled."""
    view = detail(positions=seen_at(x=-75.0, trip_id='trip-0748'))
    assert view.under is not None
    assert view.under.place == ''
    assert view.gauge is not None
    assert view.gauge.bus is None
    assert view.gauge.calls == CALLS


def test_a_vehicle_heard_under_an_alias_is_the_heros() -> None:
    """The hero's alias finds its vehicle."""
    hero = replace(HERO, aliases=('alias-1',))
    under = detail(
        view=open_view(hero=hero),
        positions=seen_at(x=-75.0, trip_id='alias-1'),
    ).under
    assert under is not None
    assert under.place == '1 stop away'


def test_a_stale_position_gives_no_place() -> None:
    """A report older than the limit gives no place."""
    then = START - timedelta(minutes=3)
    view = detail(positions=seen_at(x=-75.0, heard=then, fetched=then))
    assert view.under is not None
    assert view.under.place == ''
    assert view.gauge is not None
    assert view.gauge.bus is None
    assert view.gauge.calls == CALLS


@pytest.mark.parametrize('patterns', [{}, {'trip-0803': None}])
def test_without_a_pattern_the_view_is_unchanged(
    patterns: dict[str, TripPattern | None],
) -> None:
    """No loaded pattern leaves the view as it is."""
    view = open_view()
    assert detail(view=view, patterns=patterns) is view


def test_a_view_with_a_destination_gains_a_gauge_with_its_end() -> None:
    """A view with a Destination gets a gauge ending there."""
    hero = replace(HERO, arrives=START + timedelta(minutes=9))
    view = replace(
        open_view(hero=hero), destination='Rosehill', under=None,
    )
    gauge = detail(view=view).gauge
    assert gauge is not None
    assert (gauge.end, gauge.behind, gauge.words) == (
        1, ('Mays Hill',), '1 stop away',
    )


def test_the_stand_alone_finds_no_call() -> None:
    """A stand's own ID matches no call, as a pin's origin would be."""
    view = open_view()
    assert detail(view=view, station=STAND) is view


@pytest.mark.parametrize(('origin', 'station', 'run'), [
    (STAND, FROM, RUN),
    (
        '2150411',
        '215020',
        replace(
            RUN,
            stops=(
                *RUN.stops[:3],
                replace(RUN.stops[3], station='215020'),
                *RUN.stops[4:],
            ),
        ),
    ),
])
def test_a_from_under_a_station_finds_its_call(
    origin: str, station: str, run: TripPattern,
) -> None:
    """A stand or platform finds its call through its parent station."""
    state, _ = on_view(
        view=LiveView.STOP,
        pin=Pin(origin=place(origin), kind=SydneyMode.BUS),
        departures=(
            HERO,
            departure(
                departs=START + timedelta(minutes=6), mode=SydneyFeed.BUS,
            ),
            departure(
                departs=START + timedelta(minutes=9), mode=SydneyFeed.BUS,
            ),
        ),
        station=station,
    )
    state, _ = step(
        state,
        PatternsLoaded(
            trip_ids=frozenset({'trip-0803'}),
            patterns={'trip-0803': run},
            shapes={},
        ),
    )
    gauge = live_stop_view(state=state).gauge
    assert gauge is not None
    assert gauge.calls == ON_TIME
