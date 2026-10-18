"""The map data and positions the Map view asks for."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from paperroute.core.feeds import FeedKind, position_feeds
from paperroute.core.map_state import area_box
from paperroute.core.map_view import (
    MapInputs,
    map_departures,
    needed_patterns,
    pin_trips,
)
from paperroute.core.messages import (
    Effect,
    FetchFeed,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapData,
)
from paperroute.core.model import SYDNEY, Departure
from paperroute.core.saved import LiveView
from paperroute.core.state_model import POLL_EVERY, RETRY_AFTER, State
from paperroute.core.stop_view import StopView
from paperroute.core.tiles import ZOOMS


def elapsed(*, since: datetime | None, now: datetime, span: timedelta) -> bool:
    """
    Say whether a span of real time has passed since a moment.

    Parameters
    ----------
    since : datetime | None
        The moment, or None for never.
    now : datetime
        Current time.
    span : timedelta
        Span of real time.

    Returns
    -------
    bool
        True when `since` is None or at least `span` ago.
    """
    if since is None:
        return True
    return now.timestamp() - since.timestamp() >= span.total_seconds()


def may_load(*, state: State, kind: MapData) -> bool:
    """
    Say whether a kind of map data may be asked for again.

    Parameters
    ----------
    state : State
        State.
    kind : MapData
        Kind of map data.

    Returns
    -------
    bool
        False for `RETRY_AFTER` after that kind last failed.
    """
    return elapsed(
        since=state.map.failed_at.get(kind),
        now=state.now,
        span=RETRY_AFTER,
    )


def map_feeds(*, state: State) -> tuple[str, ...]:
    """
    List the positions feeds the Map polls.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        The feeds for the modes in the map area at the current zoom, or
        for the departures' modes when none are known there.
    """
    modes = state.map.area_modes.get(ZOOMS[state.map.zoom]) or frozenset(
        departure.mode for departure in state.departures
    )
    return position_feeds(modes=modes)


def map_inputs(*, state: State, stop_view: StopView) -> MapInputs:
    """
    Gather what the Map view is built from.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    MapInputs
        Inputs, with only the positions of feeds the Map polls. With no
        destination, the departures are only those in the sidebar.
    """
    feeds = map_feeds(state=state)
    return MapInputs(
        stop_view=stop_view,
        departures=map_departures(
            stop_view=stop_view,
            departures=state.departures,
        ),
        centre=state.map.centre,
        zoom=state.map.zoom,
        positions={
            feed: snapshot
            for feed, snapshot in state.map.positions.items()
            if feed in feeds
        },
        patterns=state.map.patterns,
        shapes=state.map.shapes,
        now=state.now,
    )


def request_modes(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the modes in the map area once per zoom.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the zoom pending, and a LoadModes; unchanged with no
        centre, when the zoom's modes are loaded or pending, or while a
        failure backs off.
    """
    map_state = state.map
    mpp = ZOOMS[map_state.zoom]
    known = map_state.area_modes.keys() | map_state.pending_modes
    if map_state.centre is None or mpp in known:
        return state, ()
    if not may_load(state=state, kind=MapData.MODES):
        return state, ()
    pending = replace(map_state, pending_modes=map_state.pending_modes | {mpp})
    load = LoadModes(mpp=mpp, box=area_box(centre=map_state.centre, mpp=mpp))
    return replace(state, map=pending), (load,)


def request_shapes(
    *, state: State, departures: Sequence[Departure]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the departures' shapes not yet asked for.

    Parameters
    ----------
    state : State
        State.
    departures : Sequence[Departure]
        The departures the Map shows.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the shapes requested, and a LoadShapes when any are new
        and no failure is backing off.
    """
    asked = state.map.requested_shapes
    wanted = {dep.shape_id for dep in departures if dep.shape_id}
    new = frozenset(wanted) - asked
    if not new or not may_load(state=state, kind=MapData.SHAPES):
        return state, ()
    map_state = replace(state.map, requested_shapes=asked | new)
    return replace(state, map=map_state), (LoadShapes(shape_ids=new),)


def request_positions(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Fetch the positions feeds the Map needs and hasn't fetched lately.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the fetch times recorded, and a FetchFeed for each feed
        never fetched or last fetched `POLL_EVERY` or more ago.
    """
    fetched_at = state.map.fetched_at
    due = [
        feed
        for feed in map_feeds(state=state)
        if elapsed(since=fetched_at.get(feed), now=state.now, span=POLL_EVERY)
    ]
    fetched_at = {**fetched_at, **dict.fromkeys(due, state.now)}
    fetches = (FetchFeed(feed=feed, kind=FeedKind.POSITIONS) for feed in due)
    map_state = replace(state.map, fetched_at=fetched_at)
    return replace(state, map=map_state), tuple(fetches)


def request_patterns(
    *, state: State, wanted: frozenset[str]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the trip patterns needed and neither loaded nor pending.

    Parameters
    ----------
    state : State
        State.
    wanted : frozenset[str]
        Trip IDs the Map needs, from `needed_patterns`.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the trips pending, and a LoadPatterns for today in
        Sydney when any are new and no failure is backing off.
    """
    pending = state.map.pending_patterns
    new = wanted - frozenset(state.map.patterns) - pending
    if not new or not may_load(state=state, kind=MapData.PATTERNS):
        return state, ()
    map_state = replace(state.map, pending_patterns=pending | new)
    load = LoadPatterns(trip_ids=new, day=state.now.astimezone(SYDNEY).date())
    return replace(state, map=map_state), (load,)


def pruned(
    *, state: State, wanted: frozenset[str], departures: Sequence[Departure]
) -> State:
    """
    Drop the patterns and shapes the Map no longer needs.

    Parameters
    ----------
    state : State
        State.
    wanted : frozenset[str]
        Trip IDs whose patterns the Map needs.
    departures : Sequence[Departure]
        The departures the Map shows.

    Returns
    -------
    State
        State keeping only the wanted patterns, and the shapes of those
        patterns and of the departures. Shape requests are trimmed to
        match, so a dropped shape can be asked for again.
    """
    patterns = {
        trip_id: pattern
        for trip_id, pattern in state.map.patterns.items()
        if trip_id in wanted
    }
    kept = {
        pattern.shape_id
        for pattern in patterns.values()
        if pattern is not None and pattern.shape_id
    } | {dep.shape_id for dep in departures if dep.shape_id}
    map_state = replace(
        state.map,
        patterns=patterns,
        shapes={
            shape_id: shape
            for shape_id, shape in state.map.shapes.items()
            if shape_id in kept
        },
        requested_shapes=state.map.requested_shapes & kept,
    )
    return replace(state, map=map_state)


def awaiting_fetch(
    *, state: State, departures: Sequence[Departure]
) -> frozenset[str]:
    """
    List the trips seen in positions feeds with a newer fetch in flight.

    Parameters
    ----------
    state : State
        State.
    departures : Sequence[Departure]
        The departures the Map shows.

    Returns
    -------
    frozenset[str]
        Trip IDs of the vehicles in each stored download that is older
        than its feed's latest fetch, apart from the pin's trips.
    """
    fetched_at = state.map.fetched_at
    return frozenset(
        vehicle.trip_id
        for feed, snapshot in state.map.positions.items()
        if fetched_at.get(feed, snapshot.fetched_at) > snapshot.fetched_at
        for vehicle in snapshot.vehicles
    ) - pin_trips(departures=departures)


def map_effects(
    *, state: State, stop_view: StopView
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the map data the Map view needs, when it is shown.

    Patterns and shapes the Map no longer needs are dropped, so memory
    stays bounded. Stale vehicles' patterns wait while their feed has a
    newer fetch in flight.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the requests recorded, and the effects; nothing on the
        Stop view.
    """
    if state.view is not LiveView.MAP:
        return state, ()
    inputs = map_inputs(state=state, stop_view=stop_view)
    shown = inputs.departures
    wanted = needed_patterns(inputs=inputs)
    state = pruned(state=state, wanted=wanted, departures=shown)
    state, modes = request_modes(state=state)
    state, shapes = request_shapes(state=state, departures=shown)
    state, fetches = request_positions(state=state)
    state, loads = request_patterns(
        state=state,
        wanted=wanted - awaiting_fetch(state=state, departures=shown),
    )
    return state, (*modes, *shapes, *fetches, *loads)
