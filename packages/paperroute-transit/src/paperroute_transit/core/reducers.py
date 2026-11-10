"""Transit's handlers for the ticks, touches and events the kit hands it."""

from dataclasses import replace
from datetime import datetime
from typing import Final

from paperroute_device.core.device_events import ConfirmAsked
from paperroute_device.core.kit_events import SettingsPosted
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_transit.core.alert_model import AlertsFetched, BetweenLoaded
from paperroute_transit.core.alert_state import (
    alerts_knob,
    between_requests,
    on_alerts,
    rematched,
)
from paperroute_transit.core.auto_pins import auto_step, with_override
from paperroute_transit.core.chains import all_departures
from paperroute_transit.core.chooser_steps import (
    chooser_knob,
    chooser_retimed,
    picked_reloads,
)
from paperroute_transit.core.city import City
from paperroute_transit.core.cycle import answered, answered_late
from paperroute_transit.core.device import on_settings
from paperroute_transit.core.knobs import knob_moved
from paperroute_transit.core.map_state import MapState
from paperroute_transit.core.messages import (
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    DeparturesFailed,
    DeparturesLoaded,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    HolidaysLoaded,
    LineFailed,
    LineLoaded,
    LoadDepartures,
    LoadHolidays,
    MapDataFailed,
    ModesLoaded,
    PatternsLoaded,
    PinDeleted,
    PinEdit,
    PinMoved,
    PinSaved,
    PinsOrdered,
    PlacesFailed,
    PlacesMatched,
    PositionsFetched,
    ServiceSpanLoaded,
    SettingsChanged,
    ShapesLoaded,
    StripFailed,
    StripLoaded,
    TimetableRebuilt,
    TimetableSeen,
)
from paperroute_transit.core.model import same_place
from paperroute_transit.core.pin_edits import on_pin_edit
from paperroute_transit.core.places import (
    feeds_of,
    is_stale,
    match_failed,
    matched,
    places_of,
    rematch,
    rematch_effects,
    shown,
    with_place,
)
from paperroute_transit.core.quiet import span_loaded, span_requests
from paperroute_transit.core.rebuild import (
    rebuild_effects,
    rebuilt,
    seen_schema,
)
from paperroute_transit.core.rhythm import holidays_loaded
from paperroute_transit.core.saved import Saved
from paperroute_transit.core.state_data import (
    on_chooser_event,
    on_map_event,
    on_strip,
)
from paperroute_transit.core.state_model import (
    FEED_IDLE,
    Board,
    State,
    elapsed,
    load_window,
    restarted,
    runs_short,
)
from paperroute_transit.core.state_views import settled

PERSON_EVENTS: Final[tuple[type, ...]] = (
    KnobTurned,
    KnobPressed,
    KnobHeld,
    KnobHeldTurned,
    KnobsHeld,
    ConfirmAsked,
    PinSaved,
    SettingsChanged,
    SettingsPosted,
)


def initial_state(*, saved: Saved, now: datetime, city: City) -> State:
    """
    Make the state at start-up, centring the map on the shown From.

    Parameters
    ----------
    saved : Saved
        The pins, place and view to start with, with the places'
        names and points filled in.
    now : datetime
        Start time.
    city : City
        The city the app runs for.

    Returns
    -------
    State
        State, the map with no knob step. A current pin out of range is
        clamped, and with no place to show the city's default pin is
        unpinned.
    """
    state = State(
        now=now,
        places=places_of(saved=saved, city=city),
        city=city,
        view=saved.view,
        settings=saved.settings,
        quota=saved.quota,
        auto=saved.auto,
    )
    centre = shown(state=state).origin.point
    return replace(state, map=MapState(centre=centre))


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
    start, end = load_window(now=now, zone=state.city.zone)
    load = LoadDepartures(
        pin=shown(state=state),
        start=start,
        end=end,
        timetable=state.saving.timetable,
        skip_school=state.settings.board.skip_school_buses,
        skip_school_pins=state.settings.board.school_off_route_pins,
    )
    board = replace(state.board, loading=True)
    return replace(state, board=board), (load,)


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


def tick_loads(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the service spans and departures a tick finds due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State marked loading, and the span, departure and chooser loads.
    """
    state, spans = span_requests(state=state)
    state, loads = reload_effects(state=state)
    state, picked = picked_reloads(state=state)
    return state, (*spans, *loads, *picked)


def on_loaded(
    *, state: State, event: DeparturesLoaded
) -> tuple[State, tuple[Effect, ...]]:
    """
    Store departures and the feeds their modes need.

    A load whose window has already ended, as after the clock jumped
    forward, is dropped and a new one becomes due.

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
    if event.until.timestamp() <= state.now.timestamp():
        return replace(state, board=replace(state.board, loading=False)), ()
    board = Board(
        departures=event.departures, loaded_until=event.until, legs=event.legs,
    )
    feeds = feeds_of(
        departures=all_departures(
            departures=event.departures, legs=event.legs,
        ),
        plan=state.city.feeds,
    )
    state = rematched(state=replace(state, board=board, feeds=feeds))
    state, asks = between_requests(state=state)
    return settled(state=state, effects=asks)


def on_feed(
    *, state: State, event: DeparturesFailed | FeedFetched | FeedFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Record a failed load or a realtime download, and redraw.

    A feed answering late is left for the next frame to draw.

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
    if isinstance(event, DeparturesFailed):
        board = replace(state.board, loading=False)
        return settled(state=replace(state, board=board))
    draw = not answered_late(state=state, feed=event.feed)
    state = answered(state=state, feed=event.feed)
    if isinstance(event, FeedFetched):
        snapshots = {**state.snapshots, event.feed: event.snapshot}
        state = replace(state, snapshots=snapshots)
    return settled(state=state, draw=draw)


def knob_step(
    *, state: State, event: KnobEvent
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a knob event, loading departures when the place changed.

    While the chooser is open every knob event goes to it, and the top
    press opens it; on the Alerts view the alerts take the top knob.

    Parameters
    ----------
    state : State
        State.
    event : KnobEvent
        Knob event.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when the event does nothing.
    """
    effects: tuple[Effect, ...] = ()
    after = alerts_knob(state=state, event=event)
    top_press = event == KnobPressed(knob=Knob.TOP)
    if after is None and (state.chooser is not None or top_press):
        after, effects = chooser_knob(state=state, event=event)
    elif after is None:
        after = knob_moved(state=state, event=event)
    if after is state:
        return state, ()
    return reloaded(
        before=state,
        after=with_override(before=state, after=after, event=event),
        effects=effects,
    )


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
        built or pending, no fold open, the chooser carried over,
        redrawn with the new reach asked for, and a MatchPlaces and a
        LoadHolidays when there is a timetable; unchanged for the version
        already seen.
    """
    if event.version == state.saving.timetable:
        return state, ()
    state = replace(
        state,
        board=replace(state.board, loaded_until=None, loading=False),
        saving=replace(
            state.saving, timetable=event.version, match_failed_at=None,
        ),
        strip=replace(
            state.strip,
            reaches={},
            pending=frozenset(),
            opened=frozenset(),
            context=None,
        ),
    )
    state, asks = chooser_retimed(state=state)
    if event.version is None:
        return settled(state=state, effects=asks)
    state, matches = rematch(state=state)
    holidays = LoadHolidays(timetable=event.version)
    return settled(state=state, effects=(*asks, *matches, holidays))


def on_matched(
    *, state: State, event: PlacesMatched | PlacesFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a timetable look-up, showing and loading a moved place afresh.

    Parameters
    ----------
    state : State
        State.
    event : PlacesMatched | PlacesFailed
        The places found, or a failed look-up.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects; none when nothing changed. A failed
        look-up only notes when it failed, so a later tick retries.
    """
    if isinstance(event, PlacesFailed):
        return match_failed(state=state), ()
    after = matched(state=state, found=event.found)
    if after.places == state.places:
        return state, ()
    if not same_place(first=shown(state=after), second=shown(state=state)):
        after = with_place(state=after, pin=after.places.unpinned)
    return reloaded(before=state, after=after)


def freed(*, state: State, free: bool) -> State:
    """
    Say whether the frame may be drawn at once, whatever the poll cycle.

    Parameters
    ----------
    state : State
        State.
    free : bool
        True while a person's action is handled.

    Returns
    -------
    State
        State whose cycle is free or not.
    """
    if state.cycle.free == free:
        return state
    return replace(state, cycle=replace(state.cycle, free=free))


def with_rebuild(
    *, result: tuple[State, tuple[Effect, ...]]
) -> tuple[State, tuple[Effect, ...]]:
    """
    Add a timetable rebuild to a handler's result when one is due.

    Parameters
    ----------
    result : tuple[State, tuple[Effect, ...]]
        The handler's state and effects.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The state and effects, with a RebuildTimetable when due.
    """
    state, effects = result
    state, rebuilds = rebuild_effects(state=state)
    return state, (*effects, *rebuilds)


def on_timetable_file(
    *,
    state: State,
    event: (
        TimetableSeen | TimetableRebuilt | HolidaysLoaded | ServiceSpanLoaded
    ),
) -> tuple[State, tuple[Effect, ...]]:
    """
    Note a timetable seen, a rebuild finished, holidays or a span read.

    A timetable seen or rebuilt rebuilds when due.

    A timetable seen or rebuilt redraws: the Building and Device screens
    show the timetable's state. Holidays and a span leave the frame for
    the next one. A timetable arriving makes every feed due at once.

    Parameters
    ----------
    state : State
        State.
    event : TimetableSeen | TimetableRebuilt | HolidaysLoaded
        | ServiceSpanLoaded
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects.
    """
    if isinstance(event, HolidaysLoaded):
        loaded = holidays_loaded(state=state, event=event)
        return replace(loaded, held=True), ()
    if isinstance(event, ServiceSpanLoaded):
        spanned = span_loaded(state=state, event=event)
        return replace(spanned, held=True), ()
    if isinstance(event, TimetableRebuilt):
        return settled(state=rebuilt(state=state, ok=event.ok))
    seen = seen_schema(state=state, seen=event)
    if state.rebuild.missing and not seen.rebuild.missing:
        seen = restarted(
            state=replace(
                seen, map=replace(seen.map, fetched_at={}), last_fetch=None,
            ),
        )
    after, effects = on_timetable(state=seen, event=event)
    return with_rebuild(result=settled(state=after, effects=effects))


def on_places(
    *, state: State, event: PinEdit | PlacesMatched | PlacesFailed
) -> tuple[State, tuple[Effect, ...]]:
    """
    Apply a pin edit or a timetable look-up of the places.

    Parameters
    ----------
    state : State
        Current state.
    event : PinEdit | PlacesMatched | PlacesFailed
        A pin saved, deleted or moved, the pins ordered, or a look-up.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state and effects. A refused edit changes nothing.
    """
    if isinstance(event, PlacesMatched | PlacesFailed):
        return on_matched(state=state, event=event)
    after = on_pin_edit(state=state, event=event)
    if after is state:
        return state, ()
    return reloaded(before=state, after=after)


def live_result(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]] | None:
    """
    Handle a load, a feed, alerts or a timetable change.

    Parameters
    ----------
    state : State
        Current state.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]] | None
        Next state and effects; None for any other event.
    """
    result = None
    match event:
        case DeparturesLoaded():
            result = on_loaded(state=state, event=event)
        case DeparturesFailed() | FeedFetched() | FeedFailed():
            result = on_feed(state=state, event=event)
        case AlertsFetched(feed=feed):
            answer = answered(state=state, feed=feed)
            result = settled(
                state=on_alerts(state=answer, event=event),
                draw=not answered_late(state=state, feed=feed),
            )
        case BetweenLoaded():
            result = settled(state=on_alerts(state=state, event=event))
        case (
            TimetableSeen()
            | TimetableRebuilt()
            | HolidaysLoaded()
            | ServiceSpanLoaded()
        ):
            result = on_timetable_file(state=state, event=event)
    return result


def data_result(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]] | None:
    """
    Handle pins and places, strips, chooser columns and map data.

    Parameters
    ----------
    state : State
        Current state.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]] | None
        Next state and effects; None for any other event.
    """
    result = None
    match event:
        case (
            PinSaved()
            | PinDeleted()
            | PinMoved()
            | PinsOrdered()
            | PlacesMatched()
            | PlacesFailed()
        ):
            result = on_places(state=state, event=event)
        case StripLoaded() | StripFailed():
            result = on_strip(state=state, event=event)
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


def ticked(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Advance transit's time: reload, rematch, poll and rebuild as due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state, and the span, departure, chooser and match loads,
        the reaches, map data and feeds asked for, and a rebuild when
        due, in that order.
    """
    state = auto_step(state=idle_dropped(state=state))
    state, loads = tick_loads(state=state)
    state, matches = rematch_effects(state=state)
    result = settled(state=state, effects=(*loads, *matches))
    return with_rebuild(result=result)


def app_event(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]]:
    """
    Hand an event the kit does not handle to its handler.

    Parameters
    ----------
    state : State
        Current state.
    event : Event
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        Next state and the effects to perform; nothing for a stale or
        unknown event.
    """
    if is_stale(state=state, event=event):
        return state, ()
    if isinstance(event, SettingsChanged):
        return on_settings(state=state, event=event)
    return (
        live_result(state=state, event=event)
        or data_result(state=state, event=event)
        or (state, ())
    )
