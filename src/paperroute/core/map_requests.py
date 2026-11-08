"""The map data and positions the Map and strip views ask for."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute.core.chains import all_departures
from paperroute.core.connection import live_chains
from paperroute.core.connection_map import connection_inputs, panes_inputs
from paperroute.core.map_fit import resting_zoom, steady_frame
from paperroute.core.map_state import area_box, area_frame
from paperroute.core.map_view import (
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
    MapInputs,
    map_departures,
    needed_patterns,
    pin_trips,
)
from paperroute.core.messages import (
    Effect,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapData,
)
from paperroute.core.places import shown
from paperroute.core.rhythm import frame_time, stale_after
from paperroute.core.saved import LiveView
from paperroute.core.state_model import RETRY_AFTER, State, elapsed
from paperroute.core.stop_view import StopView
from paperroute.core.strip_state import (
    picked_window,
    picking_to,
    strip_patterns,
    strip_window,
)
from paperroute_transit.core.geo import Point
from paperroute_transit.core.model import Departure, is_connection
from paperroute_transit.core.positions import PositionsSnapshot
from paperroute_transit.core.tiles import ZOOMS

MAP_VIEWS: Final[frozenset[LiveView]] = frozenset(
    {LiveView.MAP, LiveView.STRIP_MAP}
)


@dataclass(frozen=True)
class PatternAsk:
    """
    The trip patterns a map wants.

    Attributes
    ----------
    wanted : frozenset[str]
        Trip IDs whose patterns the map needs.
    drawn : tuple[Departure, ...]
        The departures the map draws.
    """

    wanted: frozenset[str]
    drawn: tuple[Departure, ...]


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


def polled_departures(*, state: State) -> Sequence[Departure]:
    """
    List the departures whose modes the view polls.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Sequence[Departure]
        The chooser's picked window, else the Strip's window on the
        Strip, else every departure loaded, every leg's included.
    """
    if state.chooser is not None:
        return picked_window(state=state)
    if state.view is LiveView.STRIP:
        return strip_window(state=state)
    return all_departures(
        departures=state.board.departures, legs=state.board.legs,
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
        While the chooser picks a Destination, the modes of its strip's
        departures. Otherwise, on a map, the feeds for the modes in the
        map area at the current zoom, or for every mode when none are
        known there (not loaded, failed or none found), less the light
        rail lines nowhere near the area, with every leg's modes for a
        connection pin; on the Strip, the modes of the strip's own
        departures. Sorted and unique.
    """
    plan = state.city.feeds
    modes = frozenset(
        departure.source for departure in polled_departures(state=state)
    )
    if not map_shown(state=state):
        return plan.position_feeds(sources=modes, box=None)
    centre, zoom = area_frame(map_state=state.map)
    mpp = ZOOMS[zoom]
    area = plan.position_feeds(
        sources=state.map.area_modes.get(mpp) or plan.position_sources(),
        box=None if centre is None else area_box(centre=centre, mpp=mpp),
    )
    # A connection's legs can run beyond the area the modes were read
    # for, so its own modes are polled as well.
    legs = (
        ()
        if state.board.legs is None
        else plan.position_feeds(sources=modes, box=None)
    )
    return tuple(sorted({*area, *legs}))


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


def pin_map_inputs(*, state: State, stop_view: StopView) -> MapInputs:
    """
    Gather what a map is built from, as for a plain pin.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.

    Returns
    -------
    MapInputs
        Inputs at the Map's width and frame time, centred on the From
        at the resting zoom, with only the positions of feeds the view
        polls and the departures from `drawn_departures`.
    """
    return MapInputs(
        stop_view=stop_view,
        departures=drawn_departures(state=state, stop_view=stop_view),
        centre=state.map.centre,
        zoom=resting_zoom(step=state.map.step),
        positions=polled_positions(state=state),
        patterns=state.map.patterns,
        shapes=state.map.shapes,
        now=frame_time(state=state),
        zone=state.city.zone,
        projection=state.city.projection,
        stale_after=stale_after(state=state),
        ends=pin_ends(state=state),
        step=state.map.step,
    )


def pin_ends(*, state: State) -> tuple[Point | None, Point | None]:
    """
    Find the shown pin's From and To points.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Point | None, Point | None]
        The points; None where the To or a point is missing.
    """
    pin = shown(state=state)
    end = pin.destination
    return pin.origin.point, None if end is None else end.point


def map_inputs(
    *,
    state: State,
    stop_view: StopView,
    width: int = MAP_WIDTH,
    asleep: bool = False,
) -> MapInputs:
    """
    Gather what the shown map is built from.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.
    width : int
        Map width in pixels.
    asleep : bool
        True to fit the map clear of the sleep band.

    Returns
    -------
    MapInputs
        A plain pin's inputs fitted to its next trip, keeping the frame
        on screen while it still holds the trip; for a connection
        pin, framed on the connection, or on both panes of a two-change
        Changes map, with the hero chain's legs as the departures.
    """
    inputs = replace(
        pin_map_inputs(state=state, stop_view=stop_view),
        width=width,
        asleep=asleep,
    )
    pin = shown(state=state)
    if not is_connection(pin=pin):
        return steady_frame(
            inputs=inputs,
            held=state.map.frame,
            held_ends=state.map.frame_ends,
        )
    chains = live_chains(state=state)
    mode = state.settings.board.connection_map
    return panes_inputs(
        inputs=connection_inputs(
            inputs=inputs, pin=pin, chains=chains, mode=mode,
        ),
        pin=pin,
        chains=chains,
        mode=mode,
    )


def request_modes(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the modes in the map area on screen once per zoom.

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
    centre, zoom = area_frame(map_state=map_state)
    mpp = ZOOMS[zoom]
    known = map_state.area_modes.keys() | map_state.pending_modes
    if centre is None or mpp in known:
        return state, ()
    if not may_load(state=state, kind=MapData.MODES):
        return state, ()
    pending = replace(map_state, pending_modes=map_state.pending_modes | {mpp})
    load = LoadModes(mpp=mpp, box=area_box(centre=centre, mpp=mpp))
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
    day = state.now.astimezone(state.city.zone).date()
    load = LoadPatterns(trip_ids=new, day=day)
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


def map_shown(*, state: State) -> bool:
    """
    Say whether a map is on screen.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True on the Map and Strip + map while the chooser is closed.
    """
    return state.view in MAP_VIEWS and state.chooser is None


def map_patterns(
    *, state: State, inputs: MapInputs | None
) -> frozenset[str]:
    """
    List the trips whose patterns the screen needs.

    Parameters
    ----------
    state : State
        State.
    inputs : MapInputs | None
        The map's inputs, or None with no map on screen.

    Returns
    -------
    frozenset[str]
        The strips' trips, with the map's needs when a map is shown.
    """
    wanted = strip_patterns(state=state)
    if inputs is not None:
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
        view without a map or while the chooser covers it.
    """
    if not map_shown(state=state):
        return state, ()
    state, modes = request_modes(state=state)
    state, shapes = request_shapes(state=state, departures=departures)
    return state, (*modes, *shapes)


def shows_vehicles(*, state: State) -> bool:
    """
    Say whether the screen draws vehicles.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True while the chooser picks a Destination, and with the chooser
        closed on every view but the Stop and Alerts views.
    """
    if state.chooser is not None:
        return picking_to(state=state) is not None
    return state.view not in {LiveView.STOP, LiveView.ALERTS}


def framed_state(*, state: State, inputs: MapInputs | None) -> State:
    """
    Record where the map is drawn.

    The area modes are kept by zoom alone, so a frame with a new centre
    forgets them.

    Parameters
    ----------
    state : State
        State.
    inputs : MapInputs | None
        The shown map's inputs, or None with no map on screen.

    Returns
    -------
    State
        State with the map's centre and zoom as its frame, and no area
        modes loaded or pending when the area's centre moved; no frame
        with no map or no centre.
    """
    frame = None
    ends = None
    if inputs is not None and inputs.centre is not None:
        frame = (inputs.centre, inputs.zoom)
        ends = inputs.ends
    if frame == state.map.frame and ends == state.map.frame_ends:
        return state
    framed = replace(state.map, frame=frame, frame_ends=ends)
    if area_frame(map_state=framed)[0] != area_frame(map_state=state.map)[0]:
        framed = replace(framed, area_modes={}, pending_modes=frozenset())
    return replace(state, map=framed)


def rebased(
    *, state: State, inputs: MapInputs | None, stop_view: StopView,
) -> State:
    """
    Drop knob detents that the drawn zoom's clamp hides.

    Parameters
    ----------
    state : State
        State.
    inputs : MapInputs | None
        The shown map's inputs, or None with no map on screen.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    State
        State whose step is the drawn zoom less the zoom the map takes
        with no step, so the next detent always moves the map;
        unchanged with no map.
    """
    if inputs is None:
        return state
    still = replace(state, map=replace(state.map, step=0))
    fit = map_inputs(state=still, stop_view=stop_view, width=inputs.width)
    return replace(state, map=replace(state.map, step=inputs.zoom - fit.zoom))


def sleep_effects(
    *, state: State, stop_view: StopView
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the shapes and patterns the sleep screen's map draws.

    Parameters
    ----------
    state : State
        State, asleep.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the requests recorded, and a LoadShapes and a
        LoadPatterns for the drawn departures' lines and stops; no
        positions, and nothing when the From has no location.
    """
    inputs = map_inputs(state=state, stop_view=stop_view, asleep=True)
    if inputs.centre is None:
        return state, ()
    state, shapes = request_shapes(state=state, departures=inputs.departures)
    state, loads = request_patterns(
        state=state,
        wanted=frozenset(dep.trip_id for dep in inputs.departures),
    )
    return state, (*shapes, *loads)


def map_effects(
    *, state: State, stop_view: StopView
) -> tuple[State, tuple[Effect, ...], PatternAsk | None]:
    """
    Ask for the map data the view needs, apart from trip patterns.

    Patterns and shapes no longer needed are dropped, so memory stays
    bounded. The patterns are asked for by `pattern_effects` once the
    positions feeds are fetched, since stale vehicles' patterns wait
    while their feed has a newer fetch in flight.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.

    Returns
    -------
    tuple[State, tuple[Effect, ...], PatternAsk | None]
        State with the requests recorded, the effects, and the patterns
        wanted; only the sleep map's from `sleep_effects` while asleep,
        and nothing on the Stop view, or while the chooser lists routes
        or picks a From. The patterns wanted are None in those cases.
    """
    if state.panel.asleep:
        state, effects = sleep_effects(state=state, stop_view=stop_view)
        return state, effects, None
    if not shows_vehicles(state=state):
        return state, (), None
    width = (
        STRIP_MAP_WIDTH if state.view is LiveView.STRIP_MAP else MAP_WIDTH
    )
    inputs = (
        map_inputs(state=state, stop_view=stop_view, width=width)
        if map_shown(state=state)
        else None
    )
    drawn = () if inputs is None else tuple(inputs.departures)
    state = framed_state(
        state=rebased(state=state, inputs=inputs, stop_view=stop_view),
        inputs=inputs,
    )
    wanted = map_patterns(state=state, inputs=inputs)
    state = pruned(state=state, wanted=wanted, departures=drawn)
    state, area = area_effects(state=state, departures=drawn)
    return state, area, PatternAsk(wanted=wanted, drawn=drawn)


def pattern_effects(
    *, state: State, ask: PatternAsk | None
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the trip patterns a map wants.

    Stale vehicles' patterns wait while their feed has a newer fetch in
    flight; the strip's own trips never wait.

    Parameters
    ----------
    state : State
        State, with the positions feeds fetched now recorded.
    ask : PatternAsk | None
        The patterns wanted, from `map_effects`.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the patterns pending, and a LoadPatterns; nothing when
        no patterns are wanted.
    """
    if ask is None:
        return state, ()
    return request_patterns(
        state=state,
        wanted=ask.wanted - held_back(state=state, departures=ask.drawn),
    )
