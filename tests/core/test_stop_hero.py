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
from paperroute_transit.core.gauge_requests import GaugeData
from paperroute_transit.core.messages import PatternsLoaded
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.route_trips import RouteTrip
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_views import live_stop_view
from paperroute_transit.core.stop_hero import route_vehicles, with_detail
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
    route_id='r_606',
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
    return seen(at={trip_id: x}, heard=heard, fetched=fetched)


def seen(
    *,
    at: dict[str, float],
    heard: datetime = START,
    fetched: datetime = START,
) -> dict[str, PositionsSnapshot]:
    """
    One positions download holding a vehicle per trip.

    Parameters
    ----------
    at : dict[str, float]
        Metres east of the origin by trip ID.
    heard : datetime
        When the vehicles reported.
    fetched : datetime
        When the feed was downloaded.

    Returns
    -------
    dict[str, PositionsSnapshot]
        The download by feed.
    """
    reports = tuple(
        vehicle(
            timestamp=int(heard.timestamp()),
            trip_id=trip_id,
            point=(x, 0.0),
        )
        for trip_id, x in at.items()
    )
    return {'buses': snapshot(fetched_at=fetched, vehicles=reports)}


def detail(**changes: object) -> StopView:
    """
    Add the detail to the open view, with any input replaced.

    Parameters
    ----------
    **changes : object
        Inputs of `with_detail` to replace; `positions` fills the
        gauge's positions.

    Returns
    -------
    StopView
        The result.
    """
    positions = changes.pop('positions', seen_at(x=-75.0))
    inputs: dict[str, object] = {
        'view': open_view(),
        'station': FROM,
        'patterns': {'trip-0803': RUN},
        'gauge': GaugeData(positions=positions),  # type: ignore[arg-type]
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
    assert view.gauge.words == '1 stop away'
    assert view.gauge.calls == CALLS


def test_a_vehicle_on_the_trip_before_gives_no_place() -> None:
    """Another trip's vehicle gives no place; the calls are still filled."""
    view = detail(positions=seen_at(x=-75.0, trip_id='trip-0748'))
    assert view.under is not None
    assert view.under.place == ''
    assert view.gauge is not None
    assert view.gauge.buses == ()
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


def test_route_vehicles_none_without_a_route_id() -> None:
    """A hero trip with no route ID matches no reported vehicle."""
    nameless = replace(RUN, route_id=None)
    assert not route_vehicles(
        pattern=nameless,
        positions=seen_at(x=-75.0),
        now=START,
        limit=timedelta(minutes=2),
    )


def test_a_stale_position_gives_no_place() -> None:
    """A report older than the limit gives no place."""
    then = START - timedelta(minutes=3)
    view = detail(positions=seen_at(x=-75.0, heard=then, fetched=then))
    assert view.under is not None
    assert view.under.place == ''
    assert view.gauge is not None
    assert view.gauge.buses == ()
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
    assert (gauge.end, gauge.words) == (6, '1 stop away')


def test_a_destination_gauge_carries_the_live_bus() -> None:
    """With a Destination, a fresh position 2 stops back is on the gauge."""
    hero = replace(HERO, arrives=START + timedelta(minutes=9))
    view = replace(
        open_view(hero=hero), destination='Rosehill', under=None,
    )
    gauge = detail(view=view, positions=seen_at(x=-450.0)).gauge
    assert gauge is not None
    assert [bus.trip_id for bus in gauge.buses] == ['trip-0803']


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


def test_a_destination_under_a_station_ends_the_gauge_at_its_call() -> None:
    """A child-stop Destination ends at its parent's call, not a tied one."""
    run = replace(
        RUN,
        stops=(
            *RUN.stops[:6],
            replace(RUN.stops[6], arrives=EIGHT + 720),
            RUN.stops[7],
        ),
    )
    hero = replace(HERO, arrives=START + timedelta(minutes=12))

    def end_with(*, station: str | None) -> int | None:
        """The gauge's end for a Destination pinned at a child stop."""
        state, _ = on_view(
            view=LiveView.STOP,
            pin=Pin(
                origin=place(STAND),
                destination=place('G-child'),
                kind=SydneyMode.BUS,
            ),
            departures=(hero,),
            station=FROM,
            destination_station=station,
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
        return gauge.end

    late = end_with(station='G')
    assert late is not None
    assert end_with(station=None) != late


def test_with_detail_puts_every_route_bus_on_the_gauge() -> None:
    """Live buses and a timetabled trip of the route all reach the gauge."""
    timetabled = RouteTrip(
        trip_id='trip-0748', day=START.date(), starts=EIGHT - 720,
        ends=EIGHT + 600, direction=0,
    )
    gauge = detail(
        patterns={
            'trip-0803': RUN,
            'trip-0748': replace(RUN, trip_id='trip-0748'),
            'trip-0818': replace(RUN, trip_id='trip-0818'),
        },
        gauge=GaugeData(
            positions=seen(at={'trip-0803': -75.0, 'trip-0818': -400.0}),
            trips=(timetabled,),
        ),
    ).gauge
    assert gauge is not None
    assert sorted(bus.trip_id for bus in gauge.buses) == [
        'trip-0748', 'trip-0803', 'trip-0818',
    ]
    assert sorted(bus.live for bus in gauge.buses) == [False, True, True]


def test_with_detail_keeps_the_hero_words() -> None:
    """Other buses on the route leave the hero's words as they were."""
    view = detail(
        patterns={
            'trip-0803': RUN,
            'trip-0818': replace(RUN, trip_id='trip-0818'),
        },
        positions=seen(at={'trip-0803': -75.0, 'trip-0818': -400.0}),
    )
    assert view.under is not None
    assert view.under.place == '1 stop away'
    assert view.gauge is not None
    assert view.gauge.words == '1 stop away'


def test_with_detail_keeps_a_bus_that_passed_the_from() -> None:
    """A bus of the trip before, past the From, stays on the gauge."""
    gauge = detail(
        patterns={
            'trip-0803': RUN,
            'trip-0748': replace(RUN, trip_id='trip-0748'),
        },
        positions=seen(at={'trip-0748': 250.0}),
    ).gauge
    assert gauge is not None
    assert [bus.trip_id for bus in gauge.buses] == ['trip-0748']
    assert gauge.buses[0].spot > gauge.start
