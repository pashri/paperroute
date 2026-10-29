"""The pure function that advances the device's state."""

from dataclasses import replace
from datetime import datetime

from paperroute.core.alert_model import AlertsFetched, BetweenLoaded
from paperroute.core.alert_state import (
    alert_polls,
    alerts_knob,
    between_requests,
    on_alerts,
    rematched,
)
from paperroute.core.auto_pins import auto_step, with_override
from paperroute.core.chains import all_departures
from paperroute.core.chooser_steps import (
    chooser_knob,
    chooser_retimed,
    picked_reloads,
    trip_feeds,
)
from paperroute.core.device import (
    on_device_event,
    power_guard,
    probe_effects,
    quiet_guard,
)
from paperroute.core.knobs import knob_moved
from paperroute.core.map_requests import map_feeds, shows_vehicles
from paperroute.core.map_state import MapState
from paperroute.core.menu import (
    confirm_expired,
    confirm_knob,
    menu_idle,
    menu_knob,
    on_asked,
    opened,
    touched,
)
from paperroute.core.messages import (
    ApiKeyChanged,
    ChooserDeparturesFailed,
    ChooserDeparturesLoaded,
    ColumnFailed,
    ColumnLoaded,
    ConfirmAsked,
    DeparturesFailed,
    DeparturesLoaded,
    DeviceProbed,
    DeviceProbeFailed,
    Effect,
    Event,
    FeedFailed,
    FeedFetched,
    HolidaysLoaded,
    HotspotFailed,
    HotspotStarted,
    JoinAsked,
    Knob,
    KnobEvent,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
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
    PowerFailed,
    PowerRequested,
    QuitRequested,
    ResetDone,
    ServiceSpanLoaded,
    SettingsChanged,
    ShapesLoaded,
    Stop,
    StripFailed,
    StripLoaded,
    Tick,
    TimetableRebuilt,
    TimetableSeen,
    WifiJoined,
    WifiJoinFailed,
    WifiSetupStopped,
)
from paperroute.core.model import same_place
from paperroute.core.pin_edits import on_pin_edit
from paperroute.core.places import (
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
from paperroute.core.polling import poll_effects, woken
from paperroute.core.quiet import span_loaded, span_requests, with_quiet
from paperroute.core.quota import quota_only, with_calls
from paperroute.core.rebuild import rebuild_effects, rebuilt, seen_schema
from paperroute.core.refresh import noted, with_full
from paperroute.core.rhythm import holidays_loaded
from paperroute.core.saved import Saved
from paperroute.core.sleep import held_awake
from paperroute.core.state_data import on_chooser_event, on_map_event, on_strip
from paperroute.core.state_model import (
    FEED_IDLE,
    Board,
    Saving,
    State,
    elapsed,
    load_window,
    runs_short,
)
from paperroute.core.state_saves import saved_effects
from paperroute.core.state_views import saved_of, settled
from paperroute.core.wifi import setup_expired, setup_knob


def initial_state(*, saved: Saved, now: datetime, stored: bool) -> State:
    """
    Make the state at start-up, centring the map on the shown From.

    Parameters
    ----------
    saved : Saved
        The pins, place and view to start with, with the places'
        names and points filled in.
    now : datetime
        Start time.
    stored : bool
        Whether `saved` came from the file; if not, the first tick writes
        it.

    Returns
    -------
    State
        State, the map with no knob step. A current pin out of range is
        clamped, and with no place to show Town Hall is unpinned.
    """
    state = State(
        now=now,
        places=places_of(saved=saved),
        view=saved.view,
        saving=Saving(
            saved=saved if stored else None,
            quota_at=now,
        ),
        settings=saved.settings,
        quota=saved.quota,
        auto=saved.auto,
    )
    centre = shown(state=state).origin.point
    return replace(state, map=MapState(centre=centre))


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
        skip_school=state.settings.skip_school_buses,
        skip_school_pins=state.settings.school_off_route_pins,
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


def on_tick(
    *, state: State, event: Tick
) -> tuple[State, tuple[Effect, ...]]:
    """
    Advance time: reload, poll, ask for map data and redraw as needed.

    Quiet hours then sleep or wake the screen.

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
    state = auto_step(state=idle_dropped(state=replace(state, now=event.now)))
    state, loads = tick_loads(state=state)
    state, polls = tick_polls(state=state)
    state, effects = settled(state=state, effects=(*loads, *polls))
    return with_quiet(result=with_full(state=state, effects=effects))


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


def tick_polls(*, state: State) -> tuple[State, tuple[Effect, ...]]:
    """
    Poll the feeds and alerts, save, rematch and probe as due.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State with each poll noted, and their effects in that order.
    """
    state, fetches = poll_effects(state=state)
    state, alerts = alert_polls(state=state)
    state, saves = saved_effects(state=state)
    state, matches = rematch_effects(state=state)
    state, probes = probe_effects(state=state)
    return state, (*fetches, *alerts, *saves, *matches, *probes)


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
    board = Board(
        departures=event.departures, loaded_until=event.until, legs=event.legs,
    )
    feeds = feeds_of(departures=all_departures(
        departures=event.departures, legs=event.legs,
    ))
    state = rematched(state=replace(state, board=board, feeds=feeds))
    state, asks = between_requests(state=state)
    return settled(state=state, effects=asks)


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


def on_touch(
    *, state: State, event: KnobEvent | KnobsHeld | ConfirmAsked
) -> tuple[State, tuple[Effect, ...]]:
    """
    Hand a touch or a phone's ask to the confirm, menu or Live view.

    Parameters
    ----------
    state : State
        State.
    event : KnobEvent | KnobsHeld | ConfirmAsked
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        A phone's ask shows a confirm; a touch goes to `panel_touch`.
        While asleep the first touch only wakes the screen, for
        `WAKE_FOR`; a knob touch during a wake moves its end on. A touch
        during a power action or a reset does nothing.
    """
    if state.panel.asleep:
        after, effects = woken(state=state, event=event)
        return held_awake(state=after), effects
    if isinstance(event, ConfirmAsked):
        return on_asked(state=state, event=event)
    if state.power is not None or state.panel.resetting is not None:
        return state, ()
    after, effects = panel_touch(state=state, event=event)
    if state.panel.awake_until is None:
        return after, effects
    return held_awake(state=after), effects


def panel_touch(
    *, state: State, event: KnobEvent | KnobsHeld
) -> tuple[State, tuple[Effect, ...]]:
    """
    Hand a touch to the confirm, Wi-Fi setup, menu or Live view.

    Parameters
    ----------
    state : State
        State, awake.
    event : KnobEvent | KnobsHeld
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        A touch answers the confirm shown, else works Wi-Fi setup, else
        works the open menu, else holding both knobs opens it, else it
        acts on the Live view; the Building screen ignores it. A touch
        while the menu is open is noted, so the menu stays open.
    """
    if state.panel.confirm is not None:
        after, effects = confirm_knob(state=state, event=event)
    elif state.panel.wifi is not None:
        return setup_touch(state=state, event=event)
    elif state.panel.menu is not None:
        after, effects = menu_knob(state=state, event=event)
    elif isinstance(event, KnobsHeld):
        after, effects = opened(state=state)
    elif state.rebuild.missing:
        return state, ()
    else:
        return on_knob(state=state, event=event)
    return touched(state=after), effects


def setup_touch(
    *, state: State, event: KnobEvent | KnobsHeld
) -> tuple[State, tuple[Effect, ...]]:
    """
    Hand a touch to Wi-Fi setup.

    Parameters
    ----------
    state : State
        State with setup open.
    event : KnobEvent | KnobsHeld
        What happened.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        The touch noted and redrawn; unchanged when setup ignored it.
    """
    after, effects = setup_knob(state=state, event=event)
    if after is state:
        return state, ()
    after, effects = settled(state=after, effects=effects)
    return touched(state=after), effects


def on_ticked(
    *, state: State, event: Tick
) -> tuple[State, tuple[Effect, ...]]:
    """
    Advance time, rebuild when due, and close what timed out.

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
    state, stops = setup_expired(state=state)
    state = menu_idle(state=confirm_expired(state=state))
    state, effects = with_rebuild(result=on_tick(state=state, event=event))
    return state, (*stops, *effects)


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
    after = noted(
        state=with_calls(state=after, effects=effects),
        event=event,
        effects=effects,
    )
    before, now = saved_of(state=state), saved_of(state=after)
    if before == now or quota_only(before=before, after=now):
        return after, effects
    saving = replace(after.saving, changed_at=after.now)
    return replace(after, saving=saving), effects


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

    Both redraw: the Building and Device screens show the timetable's
    state. A timetable arriving makes every feed due at once.

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
        return holidays_loaded(state=state, event=event), ()
    if isinstance(event, ServiceSpanLoaded):
        return span_loaded(state=state, event=event), ()
    if isinstance(event, TimetableRebuilt):
        return settled(state=rebuilt(state=state, ok=event.ok))
    seen = seen_schema(state=state, seen=event)
    if state.rebuild.missing and not seen.rebuild.missing:
        seen = replace(
            seen, map=replace(seen.map, fetched_at={}), last_fetch=None,
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


# pylint: disable-next=too-many-branches
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
    if is_stale(state=state, event=event):
        return state, ()
    match event:
        case (
            SettingsChanged()
            | PowerRequested()
            | PowerFailed()
            | ApiKeyChanged()
            | DeviceProbed()
            | DeviceProbeFailed()
            | ResetDone()
            | HotspotStarted()
            | HotspotFailed()
            | JoinAsked()
            | WifiJoined()
            | WifiJoinFailed()
            | WifiSetupStopped()
        ):
            return on_device_event(state=state, event=event)
    result = (
        live_result(state=state, event=event)
        or data_result(state=state, event=event)
        or (state, ())
    )
    return quiet_guard(result=power_guard(state=state, result=result))


def live_result(
    *, state: State, event: Event
) -> tuple[State, tuple[Effect, ...]] | None:
    """
    Handle a tick, a load, a feed, a touch, a quit or a timetable change.

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
        case Tick():
            result = on_ticked(state=state, event=event)
        case DeparturesLoaded():
            result = on_loaded(state=state, event=event)
        case DeparturesFailed() | FeedFetched() | FeedFailed():
            result = on_feed(state=state, event=event)
        case AlertsFetched() | BetweenLoaded():
            result = settled(state=on_alerts(state=state, event=event))
        case (
            KnobTurned()
            | KnobPressed()
            | KnobHeld()
            | KnobHeldTurned()
            | KnobsHeld()
            | ConfirmAsked()
        ):
            result = on_touch(state=state, event=event)
        case QuitRequested():
            result = on_quit(state=state)
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
