"""The pure function that advances the device's state."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.feeds import feed_for
from paperroute.core.knobs import knob_moved
from paperroute.core.live import resolve
from paperroute.core.map_requests import (
    MAP_VIEWS,
    elapsed,
    map_effects,
    map_feeds,
    map_inputs,
)
from paperroute.core.map_state import MapState, failed, with_patterns
from paperroute.core.map_view import MapView, build_map_view
from paperroute.core.messages import (
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    KnobHeld,
    KnobPressed,
    KnobTurned,
    LoadDepartures,
    MapDataFailed,
    MapEvent,
    MatchPlaces,
    ModesLoaded,
    PatternsLoaded,
    PlacesMatched,
    PositionsFetched,
    QuitRequested,
    Render,
    SaveState,
    ShapesLoaded,
    Stop,
    Tick,
    TimetableSeen,
)
from paperroute.core.model import same_place, shifted
from paperroute.core.places import (
    feeds_of,
    is_stale,
    matched,
    places_of,
    shown,
    shown_index,
    stops_to_match,
    with_place,
)
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state_model import (
    FEED_IDLE,
    LOOKBACK,
    POLL_EVERY,
    RELOAD_BEFORE,
    Board,
    Saving,
    State,
)
from paperroute.core.stop_view import (
    HORIZON,
    StopInputs,
    StopView,
    build_stop_view,
    pin_tag,
)
from paperroute.core.tiles import ZOOMS

SAVE_AFTER: Final[timedelta] = timedelta(seconds=3)


def initial_state(*, saved: Saved, now: datetime, stored: bool) -> State:
    """
    Make the state at start-up, centring the map on the shown From.

    Parameters
    ----------
    saved : Saved
        The pins, place, view and zoom to start with, with the places'
        names and points filled in.
    now : datetime
        Start time.
    stored : bool
        Whether `saved` came from the file; if not, the first tick writes
        it.

    Returns
    -------
    State
        State. A current pin out of range is clamped, a zoom too, and with
        no place to show Town Hall is unpinned.
    """
    state = State(
        now=now,
        places=places_of(saved=saved),
        view=saved.view,
        saving=Saving(saved=saved if stored else None),
    )
    centre = shown(state=state).origin.point
    zoom = min(max(saved.zoom, 0), len(ZOOMS) - 1)
    return replace(state, map=MapState(zoom=zoom, centre=centre))


def saved_of(*, state: State) -> Saved:
    """
    Take what the device keeps between restarts from the state.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    Saved
        The pins, the place shown, the Live view and the zoom.
    """
    places = state.places
    return Saved(
        pins=places.pins,
        current=places.current,
        unpinned=places.unpinned,
        view=state.view,
        zoom=state.map.zoom,
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
    pin = shown(state=state)
    destination = pin.destination
    resolved = [
        resolve(
            departure=dep,
            snapshot=state.snapshots.get(feed_for(mode=dep.mode) or ''),
            now=state.now,
        )
        for dep in state.board.departures
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
        inputs=StopInputs(
            origin=pin.origin.name,
            destination=None if destination is None else destination.name,
            departures=tuple(resolved),
            now=state.now,
            scroll=state.scroll,
            live_since=since,
            has_feed=bool(state.feeds) or not state.board.departures,
            loaded=state.board.loaded_until is not None,
            missing=pin.missing,
            tag=pin_tag(
                index=shown_index(state=state),
                total=len(state.places.pins),
            ),
        ),
    )


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
        The trip-updates feeds, then on a map its positions feeds.
    """
    if state.view not in MAP_VIEWS:
        return state.feeds
    return (*state.feeds, *map_feeds(state=state))


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
        The scrolled Stop view on the Stop view; unscrolled on the other
        views, whose sidebar always starts at the next departure.
    """
    if state.view is not LiveView.STOP:
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
        The Live view shown. Until they are built, the Strip shows the
        unscrolled Stop view and the Strip + map shows the Map.
    """
    if state.view in MAP_VIEWS:
        return build_map_view(
            inputs=map_inputs(state=state, stop_view=stop_view),
        )
    return stop_view


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
        State with the view recorded, the scroll too on the Stop view,
        and effects.
    """
    view = current_view(state=state, stop_view=stop_view)
    if isinstance(view, StopView) and state.view is LiveView.STOP:
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
    loaded_until = state.board.loaded_until
    if loaded_until is None:
        return True
    left = loaded_until.timestamp() - now.timestamp()
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
    if not reload_due(state=state, now=now) or state.board.loading:
        return state, ()
    reach = HORIZON + RELOAD_BEFORE
    start = shifted(moment=now, seconds=-int(LOOKBACK.total_seconds()))
    end = shifted(moment=now, seconds=int(reach.total_seconds()))
    load = LoadDepartures(
        pin=shown(state=state),
        start=start,
        end=end,
        timetable=state.saving.timetable,
    )
    board = replace(state.board, loading=True)
    return replace(state, board=board), (load,)


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


def saved_effects(
    *, state: State, wait: bool = True
) -> tuple[State, tuple[Effect, ...]]:
    """
    Save when the saved state differs from what was last written.

    Parameters
    ----------
    state : State
        State.
    wait : bool
        Hold the save until `SAVE_AFTER` after the last change, unless
        nothing was ever written.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the save recorded, and a SaveState; unchanged when
        nothing is due.
    """
    saving = state.saving
    current = saved_of(state=state)
    settled_long = elapsed(
        since=saving.changed_at, now=state.now, span=SAVE_AFTER
    )
    if current == saving.saved:
        return state, ()
    if wait and saving.saved is not None and not settled_long:
        return state, ()
    saving = replace(saving, saved=current)
    return replace(state, saving=saving), (SaveState(saved=current),)


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
    state, saves = saved_effects(state=state)
    return settled(state=state, effects=(*loads, *fetches, *saves))


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
    board = Board(departures=event.departures, loaded_until=event.until)
    feeds = feeds_of(departures=event.departures)
    return settled(state=replace(state, board=board, feeds=feeds))


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
            board = replace(state.board, loading=False)
            state = replace(state, board=board)
        case FeedFetched(feed=feed, snapshot=snapshot):
            state = replace(
                state, snapshots={**state.snapshots, feed: snapshot}
            )
    return settled(state=state)


def on_knob(
    *, state: State, event: KnobTurned | KnobPressed | KnobHeld
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a knob event, loading departures when the place changed.

    Parameters
    ----------
    state : State
        State.
    event : KnobTurned | KnobPressed | KnobHeld
        Knob event.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when the event does nothing.
    """
    after = knob_moved(state=state, event=event)
    if after is state:
        return state, ()
    return reloaded(before=state, after=after)


def reloaded(
    *, before: State, after: State
) -> tuple[State, tuple[Effect, ...]]:
    """
    Load departures when the place shown changed, and redraw.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after it, already showing the new place.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    loads: tuple[Effect, ...] = ()
    if not same_place(first=shown(state=after), second=shown(state=before)):
        after, loads = reload_effects(state=after)
    return settled(state=after, effects=loads)


def on_timetable(
    *, state: State, event: TimetableSeen
) -> tuple[State, tuple[Effect, ...]]:
    """
    Match every place again and reload when the timetable changed.

    Parameters
    ----------
    state : State
        State.
    event : TimetableSeen
        The timetable version in use.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with the version recorded and the departures due to reload
        (a load in flight is forgotten, its reply now stale), and a
        MatchPlaces when there is a timetable; unchanged for the
        version already seen.
    """
    if event.version == state.saving.timetable:
        return state, ()
    state = replace(
        state,
        board=replace(state.board, loaded_until=None, loading=False),
        saving=replace(state.saving, timetable=event.version),
    )
    if event.version is None:
        return state, ()
    return state, (MatchPlaces(places=stops_to_match(state=state)),)


def on_matched(
    *, state: State, event: PlacesMatched
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a timetable look-up, showing and loading a moved place afresh.

    Parameters
    ----------
    state : State
        State.
    event : PlacesMatched
        The places found.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when nothing changed.
    """
    after = matched(state=state, found=event.found)
    if after.places == state.places:
        return state, ()
    if not same_place(first=shown(state=after), second=shown(state=state)):
        after = with_place(state=after, pin=after.places.unpinned)
    return reloaded(before=state, after=after)


def on_quit(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Stop, saving any unsaved change first.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Stopped state, and a SaveState if needed, then Stop.
    """
    state, saves = saved_effects(state=state, wait=False)
    return replace(state, running=False), (*saves, Stop())


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
    Apply one event, noting when the saved state changed.

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
    after, effects = dispatched(state=state, event=event)
    if after is state or saved_of(state=after) == saved_of(state=state):
        return after, effects
    saving = replace(after.saving, changed_at=after.now)
    return replace(after, saving=saving), effects


def dispatched(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]]:
    """
    Hand an event to its handler.

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
    if is_stale(state=state, event=event):
        return result
    match event:
        case Tick():
            result = on_tick(state=state, event=event)
        case DeparturesLoaded():
            result = on_loaded(state=state, event=event)
        case DeparturesFailed() | FeedFetched() | FeedFailed():
            result = on_feed(state=state, event=event)
        case KnobTurned() | KnobPressed() | KnobHeld():
            result = on_knob(state=state, event=event)
        case QuitRequested():
            result = on_quit(state=state)
        case TimetableSeen():
            result = on_timetable(state=state, event=event)
        case PlacesMatched():
            result = on_matched(state=state, event=event)
        case (
            ShapesLoaded()
            | PatternsLoaded()
            | ModesLoaded()
            | PositionsFetched()
            | MapDataFailed()
        ):
            result = on_map_event(state=state, event=event)
    return result
