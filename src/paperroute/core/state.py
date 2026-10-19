"""The pure function that advances the device's state."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.chooser_steps import (
    chooser_event,
    chooser_knob,
    chooser_retimed,
    chooser_view,
    picked_reloads,
    trip_feeds,
)
from paperroute.core.chooser_view import ChooserView
from paperroute.core.knobs import knob_moved
from paperroute.core.live import LiveDeparture, live_since, resolved
from paperroute.core.map_requests import (
    map_effects,
    map_feeds,
    map_inputs,
    polled_positions,
    shows_vehicles,
)
from paperroute.core.map_state import MapState, failed, with_patterns
from paperroute.core.map_view import STRIP_MAP_WIDTH, MapView, build_map_view
from paperroute.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ChooserEvent,
    ColumnFailed,
    ColumnLoaded,
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    FetchFeed,
    Knob,
    KnobHeld,
    KnobPressed,
    KnobTurned,
    LineFailed,
    LineLoaded,
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
    StripFailed,
    StripLoaded,
    Tick,
    TimetableSeen,
)
from paperroute.core.model import same_place
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
    POLL_EVERY,
    Board,
    Saving,
    State,
    elapsed,
    load_window,
    runs_short,
)
from paperroute.core.stop_view import (
    StopInputs,
    StopView,
    build_stop_view,
    pin_tag,
)
from paperroute.core.strip_model import StripInputs, StripMapView, StripView
from paperroute.core.strip_opening import with_folds
from paperroute.core.strip_state import (
    clamped,
    request_strips,
    strip_failed,
    strip_inputs,
    strip_loaded,
)
from paperroute.core.strip_view import build_strip_pane, build_strip_view
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


def live_departures(*, state: State) -> tuple[LiveDeparture, ...]:
    """
    Apply the trip updates to the loaded departures.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[LiveDeparture, ...]
        Every departure, with its feed's live times where known.
    """
    return resolved(
        departures=state.board.departures,
        snapshots=state.snapshots,
        now=state.now,
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
    return build_stop_view(
        inputs=StopInputs(
            origin=pin.origin.name,
            destination=None if destination is None else destination.name,
            departures=live_departures(state=state),
            now=state.now,
            scroll=state.scroll,
            live_since=live_since(
                snapshots=state.snapshots, feeds=state.feeds, now=state.now,
            ),
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
    List every feed the screen polls.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[str, ...]
        The trip-updates feeds, then the positions feeds of a screen that
        draws vehicles.
    """
    trips = trip_feeds(state=state)
    if not shows_vehicles(state=state):
        return trips
    return (*trips, *map_feeds(state=state))


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


def shown_strip(*, state: State, stop_view: StopView) -> StripInputs:
    """
    Gather what the shown place's Strip views are built from.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's unscrolled Stop view.

    Returns
    -------
    StripInputs
        Inputs with live departures and the polled feeds' positions.
    """
    return strip_inputs(
        state=state,
        stop_view=stop_view,
        departures=live_departures(state=state),
        positions=polled_positions(state=state),
    )


def current_view(
    *, state: State, stop_view: StopView
) -> StopView | MapView | StripView | StripMapView | ChooserView:
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
    StopView | MapView | StripView | StripMapView | ChooserView
        The open chooser's screen, else the Live view shown; the Strip +
        map's map at its own width.
    """
    if state.chooser is not None:
        return chooser_view(state=state, chooser=state.chooser)
    match state.view:
        case LiveView.MAP:
            inputs = map_inputs(state=state, stop_view=stop_view)
            return build_map_view(inputs=inputs)
        case LiveView.STRIP:
            strip = shown_strip(state=state, stop_view=stop_view)
            return build_strip_view(inputs=strip)
        case LiveView.STRIP_MAP:
            inputs = map_inputs(state=state, stop_view=stop_view)
            strip = shown_strip(state=state, stop_view=stop_view)
            return StripMapView(
                map=build_map_view(
                    inputs=replace(inputs, width=STRIP_MAP_WIDTH),
                ),
                pane=build_strip_pane(inputs=strip),
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
    Ask for the reaches and map data needed, and redraw.

    The Stop view is built once, the folds the strip on screen shows are
    opened, and the Strip's scroll is clamped to its reach first.

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
    banner = stop_view.banner is not None
    state, strips = request_strips(state=clamped(
        state=with_folds(state=state, banner=banner), banner=banner,
    ))
    state, requests = map_effects(state=state, stop_view=stop_view)
    return rendered(
        state=state,
        stop_view=stop_view,
        effects=(*effects, *strips, *requests),
    )


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
        True when there are feeds to poll and POLL_EVERY of real time has
        passed since the last fetch.
    """
    return bool(trip_feeds(state=state)) and elapsed(
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
    due = runs_short(loaded_until=state.board.loaded_until, now=now)
    if not due or state.board.loading:
        return state, ()
    start, end = load_window(now=now)
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
    fetches = tuple(
        FetchFeed(feed=feed) for feed in trip_feeds(state=state)
    )
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
    state, picked = picked_reloads(state=state)
    state, fetches = poll_effects(state=state)
    state, saves = saved_effects(state=state)
    return settled(
        state=state, effects=(*loads, *picked, *fetches, *saves),
    )


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

    While the chooser is open every knob event goes to it, and the top
    press opens it.

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
    effects: tuple[Effect, ...] = ()
    if state.chooser is not None or event == KnobPressed(knob=Knob.TOP):
        after, effects = chooser_knob(state=state, event=event)
    else:
        after = knob_moved(state=state, event=event)
    if after is state:
        return state, ()
    return reloaded(before=state, after=after, effects=effects)


def reloaded(
    *, before: State, after: State, effects: tuple[Effect, ...] = ()
) -> tuple[State, tuple[Effect, ...]]:
    """
    Load departures when the place shown changed, and redraw.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after it, already showing the new place.
    effects : tuple[Effect, ...]
        Effects so far.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    loads: tuple[Effect, ...] = ()
    if not same_place(first=shown(state=after), second=shown(state=before)):
        after, loads = reload_effects(state=after)
    return settled(state=after, effects=(*effects, *loads))


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
        State with the version recorded, the departures due to reload
        (a load in flight is forgotten, its reply now stale), no reaches
        built or pending and the chooser carried over, redrawn with the
        new reach asked for, and a MatchPlaces when there is a
        timetable; unchanged for the version already seen.
    """
    if event.version == state.saving.timetable:
        return state, ()
    state = replace(
        state,
        board=replace(state.board, loaded_until=None, loading=False),
        saving=replace(state.saving, timetable=event.version),
        strip=replace(state.strip, reaches={}, pending=frozenset()),
    )
    state, asks = chooser_retimed(state=state)
    if event.version is None:
        return settled(state=state, effects=asks)
    rematch = MatchPlaces(places=stops_to_match(state=state))
    return settled(state=state, effects=(*asks, rematch))


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


def on_chooser_event(
    *, state: State, event: ChooserEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a chooser load's result, asking for what comes next.

    Parameters
    ----------
    state : State
        State.
    event : ChooserEvent
        A column, line or departures loaded or failed.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when nothing changed.
    """
    after, effects = chooser_event(state=state, event=event)
    if after is state:
        return state, ()
    return settled(state=after, effects=effects)


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
        case StripLoaded():
            result = settled(state=strip_loaded(state=state, event=event))
        case StripFailed():
            result = (strip_failed(state=state, event=event), ())
        case (
            ColumnLoaded()
            | ColumnFailed()
            | LineLoaded()
            | LineFailed()
            | ChooserDeparturesLoaded()
            | ChooserDeparturesFailed()
        ):
            result = on_chooser_event(state=state, event=event)
        case (
            ShapesLoaded()
            | PatternsLoaded()
            | ModesLoaded()
            | PositionsFetched()
            | MapDataFailed()
        ):
            result = on_map_event(state=state, event=event)
    return result
