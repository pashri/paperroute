"""Golden gauge scenes."""

from dataclasses import replace
from typing import Final

from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import (
    START,
    pattern,
    place,
    snapshot,
    vehicle,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import on_view
from paperroute_transit.core.messages import PositionsFetched
from paperroute_transit.core.model import Pin
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_model import State
from tests.core.state.helpers import BUS_POSITIONS, CENTRE
from tests.core.test_stop_hero import FROM, STAND
from tests.core.test_stop_hero import stop as call_at
from tests.golden.scenes.base import bus_departures
from tests.golden.scenes.strips import patterns_loaded

GAUGE_AHEAD: Final[tuple[tuple[str, str], ...]] = (
    ('c1', 'Parramatta Rd Bef Catherine St'),
    ('c2', 'Parramatta Rd Bef Johnston St'),
    ('c3', 'Parramatta Rd Aft Johnston St'),
    ('c4', 'Parramatta Rd Bef Lyons Rd'),
    ('c5', 'Parramatta Rd Opp Univ Of Sydney'),
    ('c6', 'Broadway Central Park'),
    ('c7', 'Harris St Ultimo'),
    ('c8', 'Quay St Haymarket'),
    ('c9', 'Elizabeth St Museum'),
    ('c10', 'Oxford St Taylor Square'),
    ('c11', 'Flinders St Moore Park'),
    ('c12', 'Cleveland St Redfern'),
    ('c13', 'Regent St Chippendale'),
    ('c14', 'George St Railway Square'),
    ('c15', 'Castlereagh St Market'),
    ('c16', 'Bathurst St Town Hall'),
    ('c17', 'Liverpool St Hyde Park'),
    ('c18', 'College St Hyde Park'),
    ('pitt', 'Pitt St At Barlow St'),
    ('c20', 'Hay St/Belmore Park'),
)


def gauge_run() -> TripPattern:
    """
    The long ride of the gauge scenes, two stops before the From.

    Returns
    -------
    TripPattern
        The hero's trip.
    """
    calls_ahead = tuple(
        call_at(
            at=180 + 120 * number,
            x=150.0 * number,
            station=station,
            name=name,
        )
        for number, (station, name) in enumerate(GAUGE_AHEAD, start=1)
    )
    return pattern(
        trip_id='trip-0803',
        route_id='r_606',
        stops=(
            call_at(at=-60, x=-300.0, station='A', name='Johnston St'),
            call_at(at=60, x=-150.0, station='B', name='Norton Plaza'),
            call_at(
                at=180, x=0.0, station=FROM, name='Leichhardt PS, Norton St',
            ),
            *calls_ahead,
        ),
    )


def gauge_pin() -> State:
    """
    Show the Stop view of a Destination pin with a long ride ahead.

    Returns
    -------
    State
        State at `START`, before the hero's trip loads.
    """
    state, _ = on_view(
        view=LiveView.STOP,
        pin=Pin(
            origin=place(STAND, name='Leichhardt PS, Norton St', point=CENTRE),
            destination=place('pitt', name='Pitt St At Barlow St'),
            kind=SydneyMode.BUS,
        ),
        departures=bus_departures(ride=38),
        station=FROM,
    )
    return state


def stop_gauge() -> State:
    """
    Show the Stop view of a Destination pin with a long ride ahead.

    Returns
    -------
    State
        State at `START`.
    """
    return patterns_loaded(
        state=gauge_pin(), patterns={'trip-0803': gauge_run()},
    )


BUSY_RIGHT: Final[tuple[float, ...]] = (
    -250.0, -120.0, 40.0, 200.0, 380.0, -60.0, 420.0, 1660.0, 1720.0,
    1810.0, 2900.0, 2960.0,
)  # metres east of the From, buses running the hero's way


BUSY_LEFT: Final[tuple[float, ...]] = (
    -200.0, 100.0, 300.0, 440.0, -280.0, 260.0, 160.0, 2950.0,
)  # metres east of the From, buses running the other way


def stop_gauge_busy() -> State:
    """
    Show the long ride with buses both ways, three in its break.

    Returns
    -------
    State
        State at `START`.
    """
    run = gauge_run()
    back = replace(run, stops=tuple(reversed(run.stops)))
    trips = {
        **{
            f'right-{number}': replace(run, trip_id=f'right-{number}')
            for number in range(len(BUSY_RIGHT))
        },
        **{
            f'left-{number}': replace(back, trip_id=f'left-{number}')
            for number in range(len(BUSY_LEFT))
        },
    }
    reports = tuple(
        vehicle(
            timestamp=int(START.timestamp()),
            trip_id=f'{way}-{number}',
            point=(x, 0.0),
        )
        for way, spots in (('right', BUSY_RIGHT), ('left', BUSY_LEFT))
        for number, x in enumerate(spots)
    )
    state, _ = step(gauge_pin(), PositionsFetched(
        feed=BUS_POSITIONS,
        snapshot=snapshot(fetched_at=START, vehicles=reports),
    ))
    return patterns_loaded(
        state=state, patterns={'trip-0803': run, **trips},
    )
