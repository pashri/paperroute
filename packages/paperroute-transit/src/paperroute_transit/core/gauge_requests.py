"""What the route gauge asks for: the route's buses, trips and patterns."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from paperroute_transit.core.messages import LoadRouteTrips
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.route_trips import RouteTrip, running
from paperroute_transit.core.stop_view import StopView, hero_departure

if TYPE_CHECKING:
    from paperroute_transit.core.state_model import State


@dataclass(frozen=True)
class GaugeData:
    """
    The route gauge's loaded data.

    Attributes
    ----------
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed the gauge reads.
    request : LoadRouteTrips | None
        The route trips asked for or loaded; None before the ask.
    trips : tuple[RouteTrip, ...]
        The trips the standing request loaded.
    """

    positions: Mapping[str, PositionsSnapshot] = field(default_factory=dict)
    request: LoadRouteTrips | None = None
    trips: tuple[RouteTrip, ...] = ()


def gauge_feeds(*, state: 'State', stop_view: StopView) -> tuple[str, ...]:
    """
    List the positions feeds the gauge polls for the hero's route.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    tuple[str, ...]
        Every positions feed of the hero's feed source, with no box
        round the From; none without a hero or when its mode is unknown.
    """
    hero = hero_departure(view=stop_view)
    if hero is None or state.city.mode(mode_id=hero.facts.kind) is None:
        return ()
    return state.city.feeds.position_feeds(sources=(hero.source,), box=None)


def gauge_trip_ids(*, state: 'State', stop_view: StopView) -> frozenset[str]:
    """
    List the trips whose patterns the gauge needs.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    frozenset[str]
        The hero's trip, the route's trips under way by the timetable,
        and the trips of live vehicles on the hero's route.
    """
    hero = hero_departure(view=stop_view)
    if hero is None:
        return frozenset()
    held = state.map.patterns.get(hero.trip_id)
    live = frozenset(
        vehicle.trip_id
        for feed in gauge_feeds(state=state, stop_view=stop_view)
        if feed in state.map.positions
        for vehicle in state.map.positions[feed].vehicles
        if held is not None and vehicle.route_id == held.route_id
    )
    under_way = running(
        trips=state.map.gauge.trips, now=state.now, zone=state.city.zone,
    )
    return (
        frozenset({hero.trip_id})
        | live
        | {trip.trip_id for trip in under_way}
    )


def route_trips_effect(
    *, state: 'State', stop_view: StopView
) -> LoadRouteTrips | None:
    """
    Ask for the hero route's trips unless they are held.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    LoadRouteTrips | None
        The request for the hero's route, source and local day; None
        without a hero or when that request already stands.
    """
    hero = hero_departure(view=stop_view)
    if hero is None:
        return None
    request = LoadRouteTrips(
        route=hero.route,
        source=hero.source,
        day=state.now.astimezone(state.city.zone).date(),
    )
    if state.map.gauge.request == request:
        return None
    return request
