"""The map data and positions the Map and strip views ask for."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Final

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
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.saved import LiveView
from paperroute.core.state_model import POLL_EVERY, RETRY_AFTER, State, elapsed
from paperroute.core.stop_view import StopView
from paperroute.core.strip_state import strip_patterns, strip_window
from paperroute.core.tiles import ZOOMS

MAP_VIEWS: Final[frozenset[LiveView]] = frozenset(
    {LiveView.MAP, LiveView.STRIP_MAP}
)


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
    List the positions feeds the view polls.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        On a map, the feeds for the modes in the map area at the current
        zoom, or for the departures' modes when none are known there; on
        the Strip, the modes of the strip's own departures.
    """
    departures = state.board.departures
    if state.view is LiveView.STRIP:
        departures = strip_window(state=state)
    modes = frozenset(departure.mode for departure in departures)
    if state.view in MAP_VIEWS:
        modes = state.map.area_modes.get(ZOOMS[state.map.zoom]) or modes
    return position_feeds(modes=modes)


def polled_positions(*, state: State) -> Mapping[str, PositionsSnapshot]:
    """
    Keep the positions downloads of the feeds the view polls.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Mapping[str, PositionsSnapshot]
        Downloads by feed, only for `map_feeds`.
    """
    feeds = map_feeds(state=state)
    return {
        feed: snapshot
        for feed, snapshot in state.map.positions.items()
        if feed in feeds
    }


def drawn_departures(
    *, state: State, stop_view: StopView
) -> tuple[Departure, ...]:
    """
    Choose the departures a map draws and asks about.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's unscrolled Stop view.

    Returns
    -------
    tuple[Departure, ...]
        With no destination, the strip's trips on Strip + map and the
        sidebar's departures on the Map; every one with a destination.
    """
    if state.view is LiveView.STRIP_MAP and stop_view.destination is None:
        return strip_window(state=state)
    return map_departures(
        stop_view=stop_view,
        departures=state.board.departures,
    )


def map_inputs(*, state: State, stop_view: StopView) -> MapInputs:
    """
    Gather what a map is built from.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    MapInputs
        Inputs at the Map's width, with only the positions of feeds the
        view polls and the departures from `drawn_departures`.
    """
    return MapInputs(
        stop_view=stop_view,
        departures=drawn_departures(state=state, stop_view=stop_view),
        centre=state.map.centre,
        zoom=state.map.zoom,
        positions=polled_positions(state=state),
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


def held_back(
    *, state: State, departures: Sequence[Departure]
) -> frozenset[str]:
    """
    List the trips whose patterns wait for a positions fetch in flight.

    Parameters
    ----------
    state : State
        State.
    departures : Sequence[Departure]
        The departures the map draws.

    Returns
    -------
    frozenset[str]
        The trips from `awaiting_fetch`, apart from the strip's own.
    """
    return awaiting_fetch(
        state=state, departures=departures
    ) - strip_patterns(state=state)


def map_patterns(*, state: State, inputs: MapInputs) -> frozenset[str]:
    """
    List the trips whose patterns the view needs.

    Parameters
    ----------
    state : State
        State.
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    frozenset[str]
        The map's needs on a map view, with the strip's trips on a strip
        view.
    """
    wanted = strip_patterns(state=state)
    if state.view in MAP_VIEWS:
        wanted |= needed_patterns(inputs=inputs)
    return wanted


def area_effects(
    *, state: State, departures: Sequence[Departure]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the map area's modes and the drawn departures' shapes.

    Parameters
    ----------
    state : State
        State.
    departures : Sequence[Departure]
        The departures the map draws.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the requests recorded, and the effects; nothing on a
        view without a map.
    """
    if state.view not in MAP_VIEWS:
        return state, ()
    state, modes = request_modes(state=state)
    state, shapes = request_shapes(state=state, departures=departures)
    return state, (*modes, *shapes)


def map_effects(
    *, state: State, stop_view: StopView
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the map data and positions the view needs.

    Patterns and shapes no longer needed are dropped, so memory stays
    bounded. Stale vehicles' patterns wait while their feed has a newer
    fetch in flight; the strip's own trips never wait.

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
    if state.view is LiveView.STOP:
        return state, ()
    inputs = map_inputs(state=state, stop_view=stop_view)
    drawn = inputs.departures if state.view in MAP_VIEWS else ()
    wanted = map_patterns(state=state, inputs=inputs)
    state = pruned(state=state, wanted=wanted, departures=drawn)
    state, area = area_effects(state=state, departures=drawn)
    state, fetches = request_positions(state=state)
    state, loads = request_patterns(
        state=state,
        wanted=wanted - held_back(state=state, departures=drawn),
    )
    return state, (*area, *fetches, *loads)
