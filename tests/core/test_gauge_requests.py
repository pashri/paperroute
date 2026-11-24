"""Tests for what the route gauge asks for."""

from dataclasses import replace
from datetime import timedelta

from paperroute_device.core.kit_events import Tick
from paperroute_testkit.core.factories import START, pattern, snapshot, vehicle
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.strip_factories import on_view
from paperroute_transit.core.gauge_requests import (
    GaugeData,
    gauge_feeds,
    gauge_trip_ids,
    route_trips_effect,
)
from paperroute_transit.core.messages import LoadRouteTrips, RouteTripsLoaded
from paperroute_transit.core.route_trips import RouteTrip
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import live_stop_view
from tests.core.test_map_requests import (
    BUS_POSITIONS,
    OPEN,
    hero_of,
    positions_of,
)

HERO_TRIP = 'trip-0803'


def on_stop(*, mode: str = 'bus', kind: str = 'bus') -> State:
    """
    Open the Stop view on a hero of one mode and feed source.

    Parameters
    ----------
    mode : str
        Mode id.
    kind : str
        Feed source.

    Returns
    -------
    State
        The state.
    """
    state, _ = on_view(
        view=LiveView.STOP,
        pin=OPEN,
        departures=hero_of(mode=mode, source=kind),
    )
    return state


def request_of(*, state: State) -> LoadRouteTrips:
    """
    Make the request the gauge asks for today.

    Parameters
    ----------
    state : State
        State on the Stop view.

    Returns
    -------
    LoadRouteTrips
        The hero's route, source and local day.
    """
    return LoadRouteTrips(
        route='606',
        source=kind_of(state=state),
        day=state.now.astimezone(state.city.zone).date(),
    )


def kind_of(*, state: State) -> str:
    """
    Name the hero's feed source.

    Parameters
    ----------
    state : State
        State on the Stop view.

    Returns
    -------
    str
        The feed source.
    """
    hero = live_stop_view(state=state).head_departures[0]
    return hero.source


def test_gauge_feeds_asks_for_the_whole_source_without_a_box() -> None:
    """The hero source's feeds are asked for with no box round the From."""
    state = on_stop()
    stop_view = live_stop_view(state=state)
    assert gauge_feeds(state=state, stop_view=stop_view) == positions_of(
        sources={'bus'},
    )


def test_gauge_feeds_asks_on_a_mode_without_stop_positions() -> None:
    """A mode that does not poll positions for the hero still is asked."""
    state = on_stop()
    modes = tuple(
        replace(spec, stop_positions=False) for spec in state.city.modes
    )
    state = replace(state, city=replace(state.city, modes=modes))
    stop_view = live_stop_view(state=state)
    assert gauge_feeds(state=state, stop_view=stop_view) == (BUS_POSITIONS,)


def test_gauge_trip_ids_includes_running_and_live_trips_of_the_route() -> None:
    """The hero, running trips and live vehicles of the route are kept."""
    state = on_stop()
    day = state.now.astimezone(state.city.zone).date()
    trips = (
        RouteTrip(
            trip_id='running', day=day, starts=0, ends=40 * 3600,
            direction=0,
        ),
        RouteTrip(
            trip_id='later', day=day, starts=44 * 3600, ends=45 * 3600,
            direction=0,
        ),
    )
    download = snapshot(
        fetched_at=START,
        vehicles=(
            vehicle(timestamp=0, trip_id='live', label='606'),
            vehicle(timestamp=0, trip_id='elsewhere', label='700'),
        ),
    )
    state = replace(
        state,
        map=replace(
            state.map,
            patterns={
                HERO_TRIP: pattern(trip_id=HERO_TRIP, route_id='r_606'),
            },
            positions={BUS_POSITIONS: download},
            gauge=GaugeData(trips=trips),
        ),
    )
    stop_view = live_stop_view(state=state)
    assert gauge_trip_ids(state=state, stop_view=stop_view) == {
        HERO_TRIP, 'running', 'live',
    }


def test_route_trips_effect_is_none_once_held() -> None:
    """The ask is made once for a route, source and day."""
    state = on_stop()
    stop_view = live_stop_view(state=state)
    state = replace(state, map=replace(state.map, gauge=GaugeData()))
    request = route_trips_effect(state=state, stop_view=stop_view)
    assert request == request_of(state=state)
    held = replace(state.map, gauge=GaugeData(request=request))
    state = replace(state, map=held)
    assert route_trips_effect(state=state, stop_view=stop_view) is None


def test_the_stop_view_asks_for_the_route_trips_once() -> None:
    """The first frame asks and a later Tick does not."""
    state, effects = on_view(
        view=LiveView.STOP,
        pin=OPEN,
        departures=hero_of(mode='bus', source='bus'),
    )
    assert request_of(state=state) in effects
    _, later = step(state, Tick(now=START + timedelta(seconds=30)))
    assert request_of(state=state) not in later


def test_reduce_route_trips_loaded_holds_them() -> None:
    """The trips are held when they answer the standing request."""
    state, _ = on_view(
        view=LiveView.STOP,
        pin=OPEN,
        departures=hero_of(mode='bus', source='bus'),
    )
    request = request_of(state=state)
    trip = RouteTrip(
        trip_id='a', day=request.day, starts=0, ends=1, direction=None,
    )
    state, _ = step(state, RouteTripsLoaded(request=request, trips=(trip,)))
    assert state.map.gauge.trips == (trip,)


def test_reduce_route_trips_loaded_drops_an_answer_to_an_old_ask() -> None:
    """Trips of a request no longer standing are left out."""
    state, _ = on_view(
        view=LiveView.STOP,
        pin=OPEN,
        departures=hero_of(mode='bus', source='bus'),
    )
    old = replace(request_of(state=state), route='700')
    state, _ = step(state, RouteTripsLoaded(request=old, trips=()))
    assert state.map.gauge.request == request_of(state=state)
    assert not state.map.gauge.trips
