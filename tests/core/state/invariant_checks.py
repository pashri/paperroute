"""Shared helpers for the . tests: invariant checks."""

from dataclasses import replace
from typing import Final, cast

from hypothesis import strategies as st

from paperroute_device.core.kit_effects import Render, SaveState, Stop
from paperroute_device.core.kit_events import Tick
from paperroute_device.core.kit_state import SAVE_WAIT, KitState
from paperroute_hardware.core.knob_events import Knob, KnobHeld
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.core.connection_factories import WORK
from paperroute_testkit.core.kit_steps import app_of
from paperroute_testkit.core.steps import last_saved, written
from paperroute_transit.core.alert_state import alert_feeds
from paperroute_transit.core.chooser import (
    ChooserState,
    Level,
    Stage,
    chosen,
    focusable,
)
from paperroute_transit.core.chooser_view import ChooserView
from paperroute_transit.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
)
from paperroute_transit.core.connection_view import ConnectionView
from paperroute_transit.core.cycle import cycle_due, needed_feeds
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.map_requests import shows_vehicles
from paperroute_transit.core.map_state import area_frame
from paperroute_transit.core.map_view import MAP_WIDTH, MapView
from paperroute_transit.core.messages import (
    Effect,
    Event,
    FetchFeed,
    LoadChooserDepartures,
    LoadColumn,
    LoadDepartures,
    LoadModes,
    LoadPatterns,
    LoadPreviews,
    LoadShapes,
    LoadStrip,
    PlacesMatched,
    ScreenView,
    TimetableSeen,
)
from paperroute_transit.core.model import is_connection, pin_key, same_place
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.places import shown
from paperroute_transit.core.quota import QUOTA_SAVE_EVERY, quota_only
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.rebuild import building_view
from paperroute_transit.core.rhythm import poll_every
from paperroute_transit.core.saved import (
    LIVE_VIEWS,
    LiveView,
    Saved,
    default_saved,
    default_settings,
    seeded,
)
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import live_stop_view, saved_of
from paperroute_transit.core.stop_view import (
    SIDEBAR_ROWS,
    VISIBLE_ROWS,
    StopView,
)
from paperroute_transit.core.strip_follow import origin_of
from paperroute_transit.core.strip_model import (
    PANE_ROWS,
    STRIP_ROWS,
    StripMapView,
    StripView,
)
from paperroute_transit.core.strip_state import (
    STRIP_VIEWS,
    keys_in_use,
    picking_to,
    scroll_range,
    shown_reach,
)
from paperroute_transit.core.tiles import ZOOMS
from tests.core.state.helpers import (
    CENTRE,
    ELSEWHERE,
    PIN,
    PINS,
    PRESS,
    check_selection,
    effects_of,
)


def check_effects(
    *, before: State, after: State, event: Event, effects: tuple[Effect, ...]
) -> None:
    """
    Assert what must hold after any single step.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    effects : tuple[Effect, ...]
        Effects returned.
    """
    assert after.scroll >= 0
    assert abs(after.map.step) < len(ZOOMS)
    assert after.view in LIVE_VIEWS
    if after.view is LiveView.STOP and isinstance(after.last_view, StopView):
        assert after.scroll == after.last_view.scroll
    assert set(after.strip.reaches) <= keys_in_use(state=after)
    check_map_requests(before=before, after=after, effects=effects)
    check_chooser(before=before, after=after, event=event, effects=effects)
    check_selection(before=before, after=after, event=event)
    for effect in effects:
        if isinstance(effect, Render):
            check_render(state=after, view=cast(ScreenView, effect.view))
            assert effect.view != before.last_view or (
                effect.full and isinstance(event, Tick)
            )
        if isinstance(effect, LoadStrip):
            check_strip_load(
                before=before, after=after, event=event, effect=effect
            )
        if isinstance(effect, LoadDepartures):
            assert not before.board.loading or not same_place(
                first=shown(state=before), second=effect.pin
            )
        if isinstance(effect, FetchFeed):
            check_fetch(before=before, after=after, effect=effect)


def check_chooser(
    *, before: State, after: State, event: Event, effects: tuple[Effect, ...]
) -> None:
    """
    Assert the chooser's cursors fit, nothing saved changes while it is
    open but the committing press, and its loads are not repeated.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    effects : tuple[Effect, ...]
        Effects returned.
    """
    chooser = after.chooser
    if chooser is not None:
        check_cursors(state=after, chooser=chooser)
        check_list_from(chooser=chooser)
    commit = event == PRESS and after.chooser is None
    if before.chooser is not None and not commit:
        if not isinstance(event, PlacesMatched):
            counted = replace(saved_of(state=after), quota=before.quota)
            assert counted == saved_of(state=before)
    swapped = (
        isinstance(event, TimetableSeen)
        and event.version != before.saving.timetable
    )
    waiting = None if before.chooser is None else before.chooser.waiting
    for load in effects_of(effects=effects, kind=LoadColumn):
        assert load.timetable == after.saving.timetable
        assert swapped or waiting is None or load.path != waiting
    for reading in effects_of(effects=effects, kind=LoadPreviews):
        assert reading.timetable == after.saving.timetable
    for asked in effects_of(effects=effects, kind=LoadChooserDepartures):
        assert chooser is not None and chooser.origin is not None
        assert chooser.stage is Stage.TO or chooser.stage is Stage.LISTS
        assert asked.pin.origin == chooser.origin


def check_list_from(*, chooser: ChooserState) -> None:
    """
    Assert a From chosen in the lists is the pressed station's.

    Parameters
    ----------
    chooser : ChooserState
        An open chooser.
    """
    if chooser.stage is not Stage.LISTS or chooser.origin is None:
        return
    level = chooser.columns[chooser.focus].level
    if level is Level.TO:
        return
    assert level is Level.STATION
    pressed = chosen(chooser=chooser, index=chooser.focus)
    assert pressed.key == chooser.origin.stop_id
    assert chooser.waiting is not None or chooser.failed


def check_cursors(*, state: State, chooser: ChooserState) -> None:
    """
    Assert every chooser cursor is within what it moves over.

    Parameters
    ----------
    state : State
        State.
    chooser : ChooserState
        Its open chooser.
    """
    assert len(chooser.cursors) == len(chooser.columns)
    for cursor, column in zip(chooser.cursors, chooser.columns, strict=True):
        assert 0 <= cursor < max(1, len(column.items))
    assert 0 <= chooser.line_cursor < max(1, len(chooser.line))
    if chooser.columns and chooser.columns[chooser.focus].level in (
        Level.STATION, Level.TO,
    ):
        choice = chosen(chooser=chooser, index=chooser.focus)
        assert focusable(choice=choice)
    if chooser.stage is not Stage.TO or chooser.key is None:
        return
    reach = state.strip.reaches.get(chooser.key)
    origin = None if reach is None else origin_of(reach=reach)
    if reach is not None and origin is not None:
        row = origin + chooser.to_cursor
        assert 0 <= row < len(reach.rows)
        assert reach.rows[row].kind is not RowKind.APPROACH


def check_strip_load(
    *, before: State, after: State, event: Event, effect: LoadStrip
) -> None:
    """
    Assert a reach is asked for on a strip view, from the timetable in
    use, and never while pending or built, unless the timetable just
    changed and forgot them.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    effect : LoadStrip
        The request.
    """
    picked = picking_to(state=after)
    if picked is None:
        assert after.chooser is None and after.view in STRIP_VIEWS
    else:
        assert effect.key == picked.key
    assert effect.timetable == after.saving.timetable
    swapped = (
        isinstance(event, TimetableSeen)
        and event.version != before.saving.timetable
    )
    if not swapped:
        assert effect.key not in before.strip.pending
        assert effect.key not in before.strip.reaches


def check_map_requests(
    *, before: State, after: State, effects: tuple[Effect, ...]
) -> None:
    """
    Assert map data is asked for only on the Map, and modes once per zoom
    while the map shows the same place in the same spot.

    Asleep, positions are never fetched, and the sleep screen's map asks
    for its shapes and patterns whatever the view. The Stop view of a pin
    with no Destination asks for positions as well as the Map.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects returned.
    """
    requests = [
        effect
        for effect in effects
        if isinstance(effect, (LoadShapes, LoadPatterns, LoadModes))
        or (
            isinstance(effect, FetchFeed)
            and effect.kind is FeedKind.POSITIONS
        )
    ]
    if after.panel.asleep:
        assert not any(isinstance(request, FetchFeed) for request in requests)
        requests = [
            request
            for request in requests
            if not isinstance(request, (LoadShapes, LoadPatterns))
        ]
    if not shows_vehicles(state=after):
        on_stop = after.view is LiveView.STOP and after.chooser is None
        assert not [
            request
            for request in requests
            if not (on_stop and isinstance(request, (FetchFeed, LoadPatterns)))
        ]
    if after.view is LiveView.STRIP or after.chooser is not None:
        assert all(
            isinstance(request, (LoadPatterns, FetchFeed))
            for request in requests
        )
    spot = area_frame(map_state=before.map)[0]
    moved = (
        before.map.centre != after.map.centre
        or spot != area_frame(map_state=after.map)[0]
        or not same_place(first=shown(state=before), second=shown(state=after))
    )
    if moved:
        return
    known = before.map.area_modes.keys() | before.map.pending_modes
    for load in effects_of(effects=effects, kind=LoadModes):
        assert load.mpp not in known


STAGE_TYPES: Final[dict[Stage, type]] = {
    Stage.LISTS: ChooserView,
    Stage.FROM: StripView,
    Stage.TO: StripView,
}


VIEW_TYPES: Final[dict[LiveView, type]] = {
    LiveView.STOP: StopView,
    LiveView.MAP: MapView,
    LiveView.STRIP: StripView,
    LiveView.STRIP_MAP: StripMapView,
}


CONNECTION_TYPES: Final[dict[LiveView, type]] = {
    LiveView.STOP: ConnectionView,
    LiveView.MAP: MapView,
    LiveView.STRIP: ConnectionStripView,
    LiveView.STRIP_MAP: ConnectionStripMapView,
}


def check_render(
    *,
    state: State,
    view: ScreenView,
) -> None:
    """
    Assert a rendered view is the sleep screen while asleep, else the
    Building screen with no timetable, else the chooser's stage or the Live
    view shown, and fits the screen, and the Strip's stored scroll is within
    its reach.

    Parameters
    ----------
    state : State
        State after the event.
    view : ScreenView
        The view drawn.
    """
    chooser = state.chooser
    if state.panel.asleep:
        assert isinstance(view, SleepView)
    elif state.rebuild.missing:
        assert view == building_view(state=state)
    elif chooser is not None:
        assert view.__class__ is STAGE_TYPES[chooser.stage]
    else:
        types = (
            VIEW_TYPES
            if not is_connection(pin=shown(state=state))
            else CONNECTION_TYPES
        )
        assert view.__class__ is types[state.view]
    match view:
        case StopView():
            assert len(view.rows) <= VISIBLE_ROWS
        case MapView():
            assert len(view.sidebar) <= SIDEBAR_ROWS
        case StripView():
            assert len(view.rows) <= STRIP_ROWS - bool(view.banner)
            destination = shown(state=state).destination
            low, high = scroll_range(
                reach=shown_reach(state=state),
                destination=(
                    None if destination is None else destination.stop_id
                ),
                banner=view.banner is not None,
            )
            assert low <= state.strip.scroll <= high
        case StripMapView():
            assert len(view.pane.rows) <= PANE_ROWS - bool(view.map.banner)
            assert view.map.base.width < MAP_WIDTH


def check_fetch(
    *, before: State, after: State, effect: FetchFeed
) -> None:
    """
    Assert a fetch is needed and either starts a cycle or joins it once.

    A cycle starts on a boundary, or off it when `cycle_reset` says so.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    effect : FetchFeed
        The fetch.
    """
    needed = (
        alert_feeds(state=after)
        if effect.kind is FeedKind.ALERTS
        else needed_feeds(
            state=after, stop_view=live_stop_view(state=after),
        )
    )
    assert effect.feed in needed
    assert effect.feed in after.cycle.asked
    assert effect.feed in after.cycle.waiting
    started = after.cycle.started != before.cycle.started
    assert not started or cycle_reset(before=before, after=after) or any(
        cycle_due(
            cycle=before.cycle, now=after.now, every=poll_every(state=state),
        )
        for state in (before, after)
    )
    assert (
        started
        or cycle_reset(before=before, after=after)
        or effect.feed not in before.cycle.asked
    )


def cycle_reset(*, before: State, after: State) -> bool:
    """
    Say whether an event let a cycle start off its boundary.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.

    Returns
    -------
    bool
        True when a timetable arrived, the panel woke, a key was set,
        the clock was set or the place shown changed.
    """
    return (
        (before.rebuild.missing and not after.rebuild.missing)
        or (before.panel.asleep and not after.panel.asleep)
        or (before.panel.key_set is False and after.panel.key_set is True)
        or (
            before.clock_wait_until is not None
            and after.clock_wait_until is None
        )
        or not same_place(first=shown(state=before), second=shown(state=after))
    )


def check_places(*, before: State, after: State, event: Event) -> None:
    """
    Assert a place is shown, current is in range and pins are unique.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after the event.
    event : Event
        The event applied.
    """
    places = after.places
    shown(state=after)
    assert 0 <= places.current < max(1, len(places.pins))
    keys = [pin_key(pin=pin) for pin in places.pins]
    assert len(set(keys)) == len(keys)
    change = len(places.pins) - len(before.places.pins)
    if change:
        assert event == KnobHeld(knob=Knob.BOTTOM)
        assert abs(change) == 1


def check_saves(
    *,
    before: KitState[State],
    after: KitState[State],
    effects: tuple[Effect, ...],
    last: Saved | None,
) -> Saved | None:
    """
    Assert every save carries the state (its quota may lag a tick), differs
    from the last one and, unless quitting or nothing was stored, waits
    SAVE_WAIT, or QUOTA_SAVE_EVERY when only the quota changed.

    Parameters
    ----------
    before : KitState[State]
        Kit state before the event.
    after : KitState[State]
        Kit state after the event.
    effects : tuple[Effect, ...]
        Effects returned.
    last : Saved | None
        What was last saved, or None before the first save.

    Returns
    -------
    Saved | None
        What is now last saved.
    """
    changed_at = before.changed_at
    now = after.device.now
    for save in effects_of(effects=effects, kind=SaveState):
        saved = written(save=save)
        counted = replace(saved, quota=after.app.quota)
        assert counted == saved_of(state=app_of(kit=after))
        assert saved != last
        last = saved
        before_saved = last_saved(kit=before)
        if Stop() in effects or before_saved is None:
            continue
        if quota_only(before=before_saved, after=saved):
            quota_at = before.written_at
            assert quota_at is not None
            assert now - quota_at >= QUOTA_SAVE_EVERY
            continue
        if changed_at is not None:
            waited = now.timestamp() - changed_at.timestamp()
            assert waited >= SAVE_WAIT.total_seconds()
    return last


@st.composite
def start_saved(draw: st.DrawFn) -> Saved:
    """
    A random place to start from.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Saved
        One pin, three pins with or without an unpinned place, the
        default, or a damaged file with no place and current out of range.
    """
    centre = draw(st.sampled_from([None, CENTRE]))
    pin = replace(PIN, origin=replace(PIN.origin, point=centre))
    options = [
        seeded(pin=pin, city=SYDNEY),
        seeded(pin=WORK, city=SYDNEY),
        default_saved(city=SYDNEY),
        replace(default_saved(city=SYDNEY), unpinned=None, current=5),
        Saved(
            settings=default_settings(city=SYDNEY),
            pins=PINS,
            current=draw(st.integers(-1, 4)),
            unpinned=draw(st.sampled_from([None, ELSEWHERE])),
            view=draw(st.sampled_from(LIVE_VIEWS)),
        ),
    ]
    return draw(st.sampled_from(options))
