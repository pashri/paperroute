"""The device's state and the pure function that advances it."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from paperroute.core.feeds import FeedKind, feed_for, position_feeds
from paperroute.core.geo import Point
from paperroute.core.live import resolve
from paperroute.core.map_state import (
    MapState,
    area_box,
    failed,
    with_patterns,
    zoomed,
)
from paperroute.core.map_view import (
    MapInputs,
    MapView,
    build_map_view,
    needed_patterns,
    pin_trips,
)
from paperroute.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    Knob,
    KnobPressed,
    KnobTurned,
    LoadDepartures,
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapData,
    MapDataFailed,
    MapEvent,
    ModesLoaded,
    PatternsLoaded,
    PositionsFetched,
    QuitRequested,
    Render,
    ShapesLoaded,
    Stop,
    Tick,
)
from paperroute.core.model import SYDNEY, Departure, Pin, shifted
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.stop_view import HORIZON, StopView, build_stop_view
from paperroute.core.tiles import ZOOMS

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)
RETRY_AFTER: Final[timedelta] = timedelta(seconds=30)
FEED_IDLE: Final[timedelta] = timedelta(minutes=10)


class LiveView(StrEnum):
    """The views the bottom knob press switches between."""

    STOP = 'stop'
    MAP = 'map'


LIVE_VIEWS: Final[tuple[LiveView, ...]] = (LiveView.STOP, LiveView.MAP)


@dataclass(frozen=True)
class State:
    """Everything the app knows."""

    pin: Pin
    origin: str
    destination: str
    now: datetime
    departures: tuple[Departure, ...] = ()
    loaded_until: datetime | None = None
    loading: bool = False
    feeds: tuple[str, ...] = ()
    snapshots: Mapping[str, RealtimeSnapshot] = field(default_factory=dict)
    last_fetch: datetime | None = None
    scroll: int = 0
    last_view: StopView | MapView | None = None
    running: bool = True
    view: LiveView = LiveView.STOP
    map: MapState = field(default_factory=MapState)


def initial_state(
    *,
    pin: Pin,
    origin: str,
    destination: str,
    now: datetime,
    centre: Point | None = None,
) -> State:
    """
    Make the state at start-up.

    Parameters
    ----------
    pin : Pin
        Pin to show.
    origin : str
        From name.
    destination : str
        Destination name.
    now : datetime
        Start time.
    centre : Point | None
        The From stop in metres, or None when it has no location.

    Returns
    -------
    State
        State.
    """
    return State(
        pin=pin,
        origin=origin,
        destination=destination,
        now=now,
        map=MapState(centre=centre),
    )


def stop_view_of(*, state: State) -> StopView:
    """
    Build the Stop view for the current state.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    StopView
        View.
    """
    resolved = [
        resolve(
            departure=dep,
            snapshot=state.snapshots.get(feed_for(mode=dep.mode) or ''),
            now=state.now,
        )
        for dep in state.departures
    ]
    fetched = [
        state.snapshots[feed].fetched_at if feed in state.snapshots else None
        for feed in state.feeds
    ]
    since = (
        None
        if None in fetched
        else min(
            (f for f in fetched if f is not None),
            key=lambda moment: moment.timestamp(),
            default=state.now,
        )
    )
    return build_stop_view(
        origin=state.origin,
        destination=state.destination,
        departures=resolved,
        now=state.now,
        scroll=state.scroll,
        live_since=since,
        has_feed=bool(state.feeds) or not state.departures,
        loaded=state.loaded_until is not None,
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


def needed_feeds(*, state: State) -> tuple[str, ...]:
    """
    List every feed the current Live view polls.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        The trip-updates feeds, then on the Map its positions feeds.
    """
    if state.view is not LiveView.MAP:
        return state.feeds
    return (*state.feeds, *map_feeds(state=state))


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
        Inputs, with only the positions of feeds the Map polls.
    """
    feeds = map_feeds(state=state)
    return MapInputs(
        stop_view=stop_view,
        departures=state.departures,
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


def live_stop_view(*, state: State) -> StopView:
    """
    Build the Stop view the current Live view shows or summarises.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    StopView
        The scrolled Stop view on the Stop view; unscrolled on the Map,
        whose sidebar always starts at the next departure.
    """
    if state.view is LiveView.MAP:
        state = replace(state, scroll=0)
    return stop_view_of(state=state)


def current_view(
    *, state: State, stop_view: StopView
) -> StopView | MapView:
    """
    Build the view for the current state.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.

    Returns
    -------
    StopView | MapView
        The Live view shown.
    """
    if state.view is LiveView.MAP:
        return build_map_view(
            inputs=map_inputs(state=state, stop_view=stop_view),
        )
    return stop_view


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


def request_shapes(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the departures' shapes not yet asked for.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the shapes requested, and a LoadShapes when any are new
        and no failure is backing off.
    """
    asked = state.map.requested_shapes
    wanted = {dep.shape_id for dep in state.departures if dep.shape_id}
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


def pruned(*, state: State, wanted: frozenset[str]) -> State:
    """
    Drop the patterns and shapes the Map no longer needs.

    Parameters
    ----------
    state : State
        State.
    wanted : frozenset[str]
        Trip IDs whose patterns the Map needs.

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
    } | {dep.shape_id for dep in state.departures if dep.shape_id}
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


def awaiting_fetch(*, state: State) -> frozenset[str]:
    """
    List the trips seen in positions feeds with a newer fetch in flight.

    Parameters
    ----------
    state : State
        State.

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
    ) - pin_trips(departures=state.departures)


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
    wanted = needed_patterns(
        inputs=map_inputs(state=state, stop_view=stop_view),
    )
    state = pruned(state=state, wanted=wanted)
    state, modes = request_modes(state=state)
    state, shapes = request_shapes(state=state)
    state, fetches = request_positions(state=state)
    state, loads = request_patterns(
        state=state,
        wanted=wanted - awaiting_fetch(state=state),
    )
    return state, (*modes, *shapes, *fetches, *loads)


def rendered(
    *,
    state: State,
    stop_view: StopView,
    effects: tuple[Effect, ...] = (),
) -> tuple[State, tuple[Effect, ...]]:
    """
    Add a Render effect when the view changed.

    Parameters
    ----------
    state : State
        State after the event.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.
    effects : tuple[Effect, ...]
        Effects so far.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the view recorded, the scroll too for a Stop view,
        and effects.
    """
    view = current_view(state=state, stop_view=stop_view)
    if isinstance(view, StopView):
        state = replace(state, scroll=view.scroll)
    if view == state.last_view:
        return state, effects
    return replace(state, last_view=view), (*effects, Render(view=view))


def settled(
    *, state: State, effects: tuple[Effect, ...] = ()
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the map data needed and redraw, building the Stop view once.

    Parameters
    ----------
    state : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects so far.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    stop_view = live_stop_view(state=state)
    state, requests = map_effects(state=state, stop_view=stop_view)
    return rendered(
        state=state,
        stop_view=stop_view,
        effects=(*effects, *requests),
    )


def reload_due(*, state: State, now: datetime) -> bool:
    """
    Say whether the loaded departures are missing or reach under HORIZON.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when less than HORIZON of real time remains loaded.
    """
    if state.loaded_until is None:
        return True
    left = state.loaded_until.timestamp() - now.timestamp()
    return left < HORIZON.total_seconds()


def poll_due(*, state: State, now: datetime) -> bool:
    """
    Say whether the trip-updates feeds should be fetched again.

    Positions feeds keep their own clocks in `MapState.fetched_at`.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when there are feeds and POLL_EVERY of real time has passed
        since the last fetch.
    """
    return bool(state.feeds) and elapsed(
        since=state.last_fetch,
        now=now,
        span=POLL_EVERY,
    )


def reload_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Load departures when they run short and no load is in flight.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State marked loading, and a LoadDepartures; unchanged otherwise.
    """
    now = state.now
    if not reload_due(state=state, now=now) or state.loading:
        return state, ()
    reach = HORIZON + RELOAD_BEFORE
    start = shifted(moment=now, seconds=-int(LOOKBACK.total_seconds()))
    end = shifted(moment=now, seconds=int(reach.total_seconds()))
    load = LoadDepartures(pin=state.pin, start=start, end=end)
    return replace(state, loading=True), (load,)


def poll_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Fetch the trip-updates feeds when a poll is due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the fetch time recorded, and a FetchFeed for each feed;
        unchanged when no poll is due.
    """
    if not poll_due(state=state, now=state.now):
        return state, ()
    fetches = tuple(FetchFeed(feed=feed) for feed in state.feeds)
    return replace(state, last_fetch=state.now), fetches


def idle_dropped(*, state: State) -> State:
    """
    Forget positions feeds not fetched for `FEED_IDLE`.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    State
        State without those feeds' positions or fetch times.
    """
    kept = {
        feed: at
        for feed, at in state.map.fetched_at.items()
        if not elapsed(since=at, now=state.now, span=FEED_IDLE)
    }
    positions = {
        feed: snapshot
        for feed, snapshot in state.map.positions.items()
        if feed in kept
    }
    map_state = replace(state.map, fetched_at=kept, positions=positions)
    return replace(state, map=map_state)


def on_tick(
    *, state: State, event: Tick
) -> tuple[State, tuple[Effect, ...]]:
    """
    Advance time: reload, poll, ask for map data and redraw as needed.

    Parameters
    ----------
    state : State
        State.
    event : Tick
        Tick.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    state = idle_dropped(state=replace(state, now=event.now))
    state, loads = reload_effects(state=state)
    state, fetches = poll_effects(state=state)
    return settled(state=state, effects=(*loads, *fetches))


def on_loaded(
    *, state: State, event: DeparturesLoaded
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store departures and the feeds their modes need.

    Parameters
    ----------
    state : State
        State.
    event : DeparturesLoaded
        Loaded departures.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    feeds = tuple(
        sorted(
            {
                feed
                for dep in event.departures
                if (feed := feed_for(mode=dep.mode)) is not None
            }
        )
    )
    return settled(
        state=replace(
            state,
            departures=event.departures,
            loaded_until=event.until,
            loading=False,
            feeds=feeds,
        )
    )


def on_feed(
    *, state: State, event: DeparturesFailed | FeedFetched | FeedFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Record a failed load or a realtime download, and redraw.

    Parameters
    ----------
    state : State
        State.
    event : DeparturesFailed | FeedFetched | FeedFailed
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    match event:
        case DeparturesFailed():
            state = replace(state, loading=False)
        case FeedFetched(feed=feed, snapshot=snapshot):
            state = replace(
                state, snapshots={**state.snapshots, feed: snapshot}
            )
    return settled(state=state)


def switched(*, state: State) -> State:
    """
    Move to the next Live view.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State showing the view after the current one in `LIVE_VIEWS`.
    """
    index = LIVE_VIEWS.index(state.view)
    return replace(state, view=LIVE_VIEWS[(index + 1) % len(LIVE_VIEWS)])


def on_knob(
    *, state: State, event: KnobTurned | KnobPressed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Switch views on a bottom press; zoom or scroll on a bottom turn.

    Parameters
    ----------
    state : State
        State.
    event : KnobTurned | KnobPressed
        Knob event; the top knob does nothing yet.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    match event:
        case KnobPressed(knob=Knob.BOTTOM):
            state = switched(state=state)
        case KnobTurned(knob=Knob.BOTTOM, steps=steps) if (
            state.view is LiveView.MAP
        ):
            state = replace(
                state, map=zoomed(map_state=state.map, steps=steps)
            )
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            state = replace(state, scroll=max(0, state.scroll + steps))
        case _:
            return state, ()
    return settled(state=state)


def on_map_event(
    *, state: State, event: MapEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store map data, ask for what is still needed, and redraw.

    Parameters
    ----------
    state : State
        State.
    event : MapEvent
        Map data or a failure to load it.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; a failure only records itself.
    """
    map_state = state.map
    match event:
        case ShapesLoaded(shapes=shapes):
            map_state = replace(
                map_state,
                shapes={**map_state.shapes, **shapes},
            )
        case PatternsLoaded():
            map_state = with_patterns(map_state=map_state, event=event)
        case ModesLoaded(mpp=mpp, modes=modes):
            map_state = replace(
                map_state,
                area_modes={**map_state.area_modes, mpp: modes},
                pending_modes=map_state.pending_modes - {mpp},
            )
        case PositionsFetched(feed=feed, snapshot=snapshot):
            map_state = replace(
                map_state,
                positions={**map_state.positions, feed: snapshot},
            )
        case MapDataFailed():
            map_state = failed(map_state=map_state, event=event, now=state.now)
            return replace(state, map=map_state), ()
    return settled(state=replace(state, map=map_state))


def step(state: State, event: Event) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply one event.

    Parameters
    ----------
    state : State
        Current state.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Next state and the effects to perform.
    """
    result: tuple[State, tuple[Effect, ...]] = (state, ())
    match event:
        case Tick():
            result = on_tick(state=state, event=event)
        case DeparturesLoaded():
            result = on_loaded(state=state, event=event)
        case DeparturesFailed() | FeedFetched() | FeedFailed():
            result = on_feed(state=state, event=event)
        case KnobTurned() | KnobPressed():
            result = on_knob(state=state, event=event)
        case QuitRequested():
            result = replace(state, running=False), (Stop(),)
        case (
            ShapesLoaded()
            | PatternsLoaded()
            | ModesLoaded()
            | PositionsFetched()
            | MapDataFailed()
        ):
            result = on_map_event(state=state, event=event)
    return result
