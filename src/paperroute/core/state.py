"""The device's state and the pure function that advances it."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final

from paperroute.core.feeds import FeedKind, feed_for, feed_kind, position_feeds
from paperroute.core.geo import MetreBox, Point, Shape
from paperroute.core.live import resolve
from paperroute.core.map_state import MapState, area_box, zoomed
from paperroute.core.map_view import (
    MapInputs,
    MapView,
    build_map_view,
    needed_patterns,
)
from paperroute.core.model import SYDNEY, Departure, Mode, Pin, shifted
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.realtime import RealtimeSnapshot
from paperroute.core.stop_view import HORIZON, StopView, build_stop_view
from paperroute.core.tiles import ZOOMS

POLL_EVERY: Final[timedelta] = timedelta(seconds=30)
RELOAD_BEFORE: Final[timedelta] = timedelta(minutes=30)
LOOKBACK: Final[timedelta] = timedelta(minutes=60)


class Knob(StrEnum):
    """The two knobs."""

    TOP = 'top'
    BOTTOM = 'bottom'


class LiveView(StrEnum):
    """The views the bottom knob press switches between."""

    STOP = 'stop'
    MAP = 'map'


LIVE_VIEWS: Final[tuple[LiveView, ...]] = (LiveView.STOP, LiveView.MAP)


@dataclass(frozen=True)
class Tick:
    """Time passed."""

    now: datetime


@dataclass(frozen=True)
class DeparturesLoaded:
    """Departures arrived from the timetable."""

    departures: tuple[Departure, ...]
    until: datetime


@dataclass(frozen=True)
class DeparturesFailed:
    """The timetable couldn't be read; the next tick tries again."""


@dataclass(frozen=True)
class FeedFetched:
    """A realtime feed was downloaded and parsed."""

    feed: str
    snapshot: RealtimeSnapshot


@dataclass(frozen=True)
class FeedFailed:
    """A realtime fetch failed."""

    feed: str
    at: datetime
    reason: str


@dataclass(frozen=True)
class KnobTurned:
    """A knob turned by some detents (negative is anticlockwise)."""

    knob: Knob
    steps: int


@dataclass(frozen=True)
class KnobPressed:
    """A knob was pressed."""

    knob: Knob


@dataclass(frozen=True)
class QuitRequested:
    """The user asked to quit (development monitor only)."""


@dataclass(frozen=True)
class ShapesLoaded:
    """Shapes arrived from the timetable."""

    shapes: Mapping[str, Shape]


@dataclass(frozen=True)
class PatternsLoaded:
    """Trip patterns and their shapes arrived from the timetable."""

    patterns: Mapping[str, TripPattern | None]
    shapes: Mapping[str, Shape]


@dataclass(frozen=True)
class ModesLoaded:
    """The modes in the map area at one zoom arrived from the timetable."""

    mpp: int
    modes: frozenset[Mode]


@dataclass(frozen=True)
class PositionsFetched:
    """A positions feed was downloaded and parsed."""

    feed: str
    snapshot: PositionsSnapshot


@dataclass(frozen=True)
class MapDataFailed:
    """Map data couldn't be read; kind is 'shapes', 'patterns' or 'modes'."""

    kind: str


MapEvent = (
    ShapesLoaded
    | PatternsLoaded
    | ModesLoaded
    | PositionsFetched
    | MapDataFailed
)
Event = (
    Tick
    | DeparturesLoaded
    | DeparturesFailed
    | FeedFetched
    | FeedFailed
    | KnobTurned
    | KnobPressed
    | QuitRequested
    | MapEvent
)


@dataclass(frozen=True)
class LoadDepartures:
    """Ask the timetable for departures."""

    pin: Pin
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FetchFeed:
    """Download a realtime feed."""

    feed: str
    kind: FeedKind = FeedKind.TRIPS


@dataclass(frozen=True)
class LoadShapes:
    """Ask the timetable for shapes."""

    shape_ids: frozenset[str]


@dataclass(frozen=True)
class LoadPatterns:
    """Ask the timetable for trip patterns around a day."""

    trip_ids: frozenset[str]
    day: date


@dataclass(frozen=True)
class LoadModes:
    """Ask the timetable for the modes in an area."""

    mpp: int
    box: MetreBox


@dataclass(frozen=True)
class Render:
    """Draw a view."""

    view: StopView | MapView


@dataclass(frozen=True)
class Stop:
    """Stop the app."""


Effect = (
    LoadDepartures
    | FetchFeed
    | LoadShapes
    | LoadPatterns
    | LoadModes
    | Render
    | Stop
)


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


def current_view(*, state: State) -> StopView | MapView:
    """
    Build the view for the current state.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    StopView | MapView
        The Live view shown.
    """
    stop_view = stop_view_of(state=state)
    if state.view is LiveView.MAP:
        return build_map_view(
            inputs=map_inputs(state=state, stop_view=stop_view),
        )
    return stop_view


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
        centre or when the zoom's modes are loaded or pending.
    """
    map_state = state.map
    mpp = ZOOMS[map_state.zoom]
    known = map_state.area_modes.keys() | map_state.pending_modes
    if map_state.centre is None or mpp in known:
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
        State with the shapes requested, and a LoadShapes when any are new.
    """
    asked = state.map.requested_shapes
    wanted = {dep.shape_id for dep in state.departures if dep.shape_id}
    new = frozenset(wanted) - asked
    if not new:
        return state, ()
    map_state = replace(state.map, requested_shapes=asked | new)
    return replace(state, map=map_state), (LoadShapes(shape_ids=new),)


def request_positions(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Fetch the positions feeds the Map needs and never fetched.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the feeds requested, and a FetchFeed for each.
    """
    asked = state.map.requested_feeds
    new = [feed for feed in map_feeds(state=state) if feed not in asked]
    map_state = replace(state.map, requested_feeds=asked | frozenset(new))
    fetches = (FetchFeed(feed=feed, kind=FeedKind.POSITIONS) for feed in new)
    return replace(state, map=map_state), tuple(fetches)


def wanted_patterns(*, state: State) -> frozenset[str]:
    """
    List the trips whose patterns the Map needs now.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    frozenset[str]
        Trip IDs, from `needed_patterns`.
    """
    return needed_patterns(
        inputs=map_inputs(state=state, stop_view=stop_view_of(state=state)),
    )


def request_patterns(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the trip patterns needed and neither loaded nor pending.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the trips pending, and a LoadPatterns for today in
        Sydney when any are new.
    """
    pending = state.map.pending_patterns
    loaded = frozenset(state.map.patterns)
    new = wanted_patterns(state=state) - loaded - pending
    if not new:
        return state, ()
    map_state = replace(state.map, pending_patterns=pending | new)
    load = LoadPatterns(trip_ids=new, day=state.now.astimezone(SYDNEY).date())
    return replace(state, map=map_state), (load,)


def map_effects(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the map data the Map view needs, when it is shown.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the requests recorded, and the effects; nothing on the
        Stop view.
    """
    if state.view is not LiveView.MAP:
        return state, ()
    state, modes = request_modes(state=state)
    state, shapes = request_shapes(state=state)
    state, fetches = request_positions(state=state)
    state, patterns = request_patterns(state=state)
    return state, (*modes, *shapes, *fetches, *patterns)


def rendered(
    *, state: State, effects: tuple[Effect, ...] = ()
) -> tuple[State, tuple[Effect, ...]]:
    """
    Add a Render effect when the view changed.

    Parameters
    ----------
    state : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects so far.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the view recorded, the scroll too for a Stop view,
        and effects.
    """
    view = current_view(state=state)
    if isinstance(view, StopView):
        state = replace(state, scroll=view.scroll)
    if view == state.last_view:
        return state, effects
    return replace(state, last_view=view), (*effects, Render(view=view))


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
    Say whether the feeds should be fetched again.

    Parameters
    ----------
    state : State
        State.
    now : datetime
        Current time.

    Returns
    -------
    bool
        True when the Live view needs feeds and POLL_EVERY of real time
        has passed since the last fetch.
    """
    if not needed_feeds(state=state):
        return False
    if state.last_fetch is None:
        return True
    since = now.timestamp() - state.last_fetch.timestamp()
    return since >= POLL_EVERY.total_seconds()


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
    Fetch every feed the Live view needs when a poll is due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the fetch time and requested positions feeds recorded,
        and a FetchFeed for each feed; unchanged when no poll is due.
    """
    if not poll_due(state=state, now=state.now):
        return state, ()
    feeds = needed_feeds(state=state)
    fetches = tuple(
        FetchFeed(feed=feed, kind=feed_kind(feed=feed)) for feed in feeds
    )
    requested = state.map.requested_feeds | {
        fetch.feed for fetch in fetches if fetch.kind is FeedKind.POSITIONS
    }
    return (
        replace(
            state,
            last_fetch=state.now,
            map=replace(state.map, requested_feeds=requested),
        ),
        fetches,
    )


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
    state = replace(state, now=event.now)
    state, loads = reload_effects(state=state)
    state, fetches = poll_effects(state=state)
    state, requests = map_effects(state=state)
    return rendered(state=state, effects=(*loads, *fetches, *requests))


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
    state, effects = map_effects(
        state=replace(
            state,
            departures=event.departures,
            loaded_until=event.until,
            loading=False,
            feeds=feeds,
        )
    )
    return rendered(state=state, effects=effects)


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
    return rendered(state=state)


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
    state, effects = map_effects(state=state)
    return rendered(state=state, effects=effects)


def with_patterns(*, state: State, event: PatternsLoaded) -> MapState:
    """
    Store loaded patterns and their shapes, and clear them from pending.

    Only the patterns the Map still needs are kept.

    Parameters
    ----------
    state : State
        State.
    event : PatternsLoaded
        Loaded patterns.

    Returns
    -------
    MapState
        The new Map state.
    """
    map_state = state.map
    wanted = wanted_patterns(state=state)
    merged = {**map_state.patterns, **event.patterns}
    return replace(
        map_state,
        shapes={**map_state.shapes, **event.shapes},
        patterns={
            trip_id: pattern
            for trip_id, pattern in merged.items()
            if trip_id in wanted
        },
        pending_patterns=map_state.pending_patterns
        - frozenset(event.patterns),
    )


def failed(*, map_state: MapState, kind: str) -> MapState:
    """
    Forget a failed map data request, so the next tick asks again.

    Parameters
    ----------
    map_state : MapState
        The Map state.
    kind : str
        'shapes', 'patterns' or 'modes'.

    Returns
    -------
    MapState
        The Map state with that kind's pending or requested set cleared;
        loaded shapes stay requested.
    """
    match kind:
        case 'shapes':
            loaded = frozenset(map_state.shapes)
            return replace(
                map_state,
                requested_shapes=loaded & map_state.requested_shapes,
            )
        case 'patterns':
            return replace(map_state, pending_patterns=frozenset())
        case 'modes':
            return replace(map_state, pending_modes=frozenset())
    return map_state


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
        New state and effects; a failure only clears its pending requests.
    """
    map_state = state.map
    match event:
        case ShapesLoaded(shapes=shapes):
            map_state = replace(
                map_state,
                shapes={**map_state.shapes, **shapes},
            )
        case PatternsLoaded():
            map_state = with_patterns(state=state, event=event)
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
        case MapDataFailed(kind=kind):
            map_state = failed(map_state=map_state, kind=kind)
            return replace(state, map=map_state), ()
    state, effects = map_effects(state=replace(state, map=map_state))
    return rendered(state=state, effects=effects)


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
