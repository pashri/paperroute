"""Build the screen a state shows, and what it keeps between restarts."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.banners import device_banners
from paperroute_device.core.device_state import Banner
from paperroute_device.core.panel_views import NoticeView
from paperroute_transit.core.alert_match import alert_banner
from paperroute_transit.core.alert_state import alerts_left, shown_alerts
from paperroute_transit.core.alerts_view import build_alerts_view
from paperroute_transit.core.auto_pins import rule_holds
from paperroute_transit.core.banners import OFFLINE_PHRASE, timetable_banner
from paperroute_transit.core.chooser_steps import chooser_view
from paperroute_transit.core.connection_screens import connection_screen
from paperroute_transit.core.cycle import cycle_effects, cycle_held
from paperroute_transit.core.live import LiveDeparture, live_since, resolved
from paperroute_transit.core.map_requests import (
    map_effects,
    map_inputs,
    pattern_effects,
    polled_positions,
)
from paperroute_transit.core.map_view import STRIP_MAP_WIDTH, build_map_view
from paperroute_transit.core.messages import Effect, ScreenView
from paperroute_transit.core.panel_views import SleepView
from paperroute_transit.core.places import shown, shown_index
from paperroute_transit.core.rebuild import building_view
from paperroute_transit.core.rhythm import frame_time, stale_after
from paperroute_transit.core.saved import LiveView, Saved
from paperroute_transit.core.sleep import sleep_view
from paperroute_transit.core.state_model import State, clock_waiting, elapsed
from paperroute_transit.core.stop_view import (
    StopInputs,
    StopView,
    banner_text,
    build_stop_view,
    pin_tag,
)
from paperroute_transit.core.strip_model import StripInputs, StripMapView
from paperroute_transit.core.strip_opening import with_folds
from paperroute_transit.core.strip_select import kept_selection
from paperroute_transit.core.strip_state import (
    clamped,
    request_strips,
    strip_inputs,
)
from paperroute_transit.core.strip_view import (
    build_strip_pane,
    build_strip_view,
)

LOADING_WAIT: Final[timedelta] = timedelta(seconds=5)


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
        The pins, the place shown, the Live view, settings and the auto
        pin state;
        the Stop view in place of the Alerts view, which needs alerts
        not kept.
    """
    places = state.places
    view = LiveView.STOP if state.view is LiveView.ALERTS else state.view
    return Saved(
        pins=places.pins,
        current=places.current,
        unpinned=places.unpinned,
        view=view,
        settings=state.settings,
        quota=state.quota,
        auto=state.auto,
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
        Every departure, with its feed's live times where known at the
        frame time.
    """
    return resolved(
        departures=state.board.departures,
        snapshots=state.snapshots,
        now=frame_time(state=state),
        limit=stale_after(state=state),
        zone=state.city.zone,
        plan=state.city.feeds,
    )


def stop_inputs(*, state: State, notice: Banner | None) -> StopInputs:
    """
    Gather what the Stop view is built from.

    Parameters
    ----------
    state : State
        State.
    notice : Banner | None
        The banner line chosen for the view, or None.

    Returns
    -------
    StopInputs
        The inputs, at the frame time.
    """
    pin = shown(state=state)
    destination = pin.destination
    frame = frame_time(state=state)
    return StopInputs(
        origin=pin.origin.name,
        destination=None if destination is None else destination.name,
        departures=live_departures(state=state),
        now=frame,
        zone=state.city.zone,
        scroll=state.scroll,
        live_since=live_since(
            snapshots=state.snapshots, feeds=state.feeds, now=frame,
        ),
        has_feed=bool(state.feeds) or not state.board.departures,
        stale_after=stale_after(state=state),
        city=state.city,
        loaded=state.board.loaded_until is not None,
        missing=pin.missing,
        notice=notice,
        alert=alert_banner(
            listed=shown_alerts(state=state),
            now=frame,
            zone=state.city.zone,
        ),
        large=state.settings.device.larger_text,
        inverted=state.settings.device.inverted_lists,
        tag=pin_tag(
            index=shown_index(state=state),
            total=len(state.places.pins),
            nickname=pin.nickname,
            auto=rule_holds(state=state),
        ),
    )


def app_banners(*, state: State) -> tuple[Banner, ...]:
    """
    List transit's banner lines, first first.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    tuple[Banner, ...]
        The timetable's end when it is near or past, then the live data
        line, the pin's alert and the no-feed line.
    """
    timetable = timetable_banner(state=state)
    head = () if timetable is None else (Banner(text=timetable),)
    inputs = stop_inputs(state=state, notice=None)
    return (*head, *banner_text(inputs=inputs))


def device_notice(*, state: State) -> Banner | None:
    """
    Choose the line that takes the banner ahead of the live data one.

    Parameters
    ----------
    state : State
        State, its device fields a mirror of the kit's.

    Returns
    -------
    Banner | None
        The first device warning, else the timetable's end when it is
        near or past, else None; the Stop view falls back to its own
        lines, so the line shown is the one the kit chooses.
    """
    timetable = timetable_banner(state=state)
    lines = (
        *device_banners(panel=state.panel, phrase=OFFLINE_PHRASE),
        *(() if timetable is None else (Banner(text=timetable),)),
    )
    return next(iter(lines), None)


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
        View, at the frame time, under the banner line the kit chooses.
    """
    return banner_stop_view(state=state, banner=device_notice(state=state))


def banner_stop_view(*, state: State, banner: Banner | None) -> StopView:
    """
    Build the Stop view for the current state under a banner line.

    Parameters
    ----------
    state : State
        State.
    banner : Banner | None
        The banner line, or None for none.

    Returns
    -------
    StopView
        View, at the frame time.
    """
    return build_stop_view(inputs=stop_inputs(state=state, notice=banner))


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
        As `banner_live_view`, under the banner line the kit chooses.
    """
    return banner_live_view(state=state, banner=device_notice(state=state))


def banner_live_view(*, state: State, banner: Banner | None) -> StopView:
    """
    Build the Stop view the current Live view shows, under a banner line.

    Parameters
    ----------
    state : State
        State.
    banner : Banner | None
        The banner line, or None for none.

    Returns
    -------
    StopView
        The scrolled Stop view on the Stop view; unscrolled on the other
        views, whose sidebar always starts at the next departure.
    """
    if state.view is not LiveView.STOP:
        state = replace(state, scroll=0)
    return banner_stop_view(state=state, banner=banner)


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
) -> ScreenView:
    """
    Build transit's live view for the current state.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.

    Returns
    -------
    ScreenView
        The Building screen with no timetable, else the open chooser's
        screen, else the Live view shown.
    """
    if state.rebuild.missing:
        return building_view(state=state)
    if state.chooser is not None:
        return chooser_view(state=state, chooser=state.chooser)
    return live_view(state=state, stop_view=stop_view)


def live_view(*, state: State, stop_view: StopView) -> ScreenView:
    """
    Build the Live view shown.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.

    Returns
    -------
    ScreenView
        The Live view; the Strip + map's map at its own width, and the
        Stop view for the Alerts view with nothing listed. A connection
        pin's own view on every view but Alerts.
    """
    if screen := connection_screen(state=state, stop_view=stop_view):
        return screen
    match state.view:
        case LiveView.MAP:
            inputs = map_inputs(state=state, stop_view=stop_view)
            return build_map_view(inputs=inputs)
        case LiveView.STRIP:
            strip = shown_strip(state=state, stop_view=stop_view)
            return build_strip_view(inputs=strip)
        case LiveView.STRIP_MAP:
            inputs = map_inputs(
                state=state, stop_view=stop_view, width=STRIP_MAP_WIDTH,
            )
            strip = shown_strip(state=state, stop_view=stop_view)
            return StripMapView(
                map=build_map_view(inputs=inputs),
                pane=build_strip_pane(inputs=strip),
            )
        case LiveView.ALERTS if listed := shown_alerts(state=state):
            return build_alerts_view(
                listed=listed,
                selected=state.alerts.selected,
                page=state.alerts.page,
                stop_view=stop_view,
                now=frame_time(state=state),
                zone=state.city.zone,
            )
    return stop_view


def loading_held(*, state: State) -> bool:
    """
    Say whether the first frame waits for the timetable to load.

    Parameters
    ----------
    state : State
        State after the event.

    Returns
    -------
    bool
        True before any frame is drawn and before departures load,
        until `LOADING_WAIT` has passed since the app started. False
        once the clock has stepped back past the start.
    """
    if state.last_view is not None or state.board.loaded_until is not None:
        return False
    started = state.refresh.started_at or state.now
    if state.now < started:
        return False
    return not elapsed(since=started, now=state.now, span=LOADING_WAIT)


def frame_held(*, state: State, view: ScreenView, menu: bool) -> bool:
    """
    Say whether a changed view waits for the poll cycle's feeds.

    Parameters
    ----------
    state : State
        State after the event.
    view : ScreenView
        The view to draw.
    menu : bool
        Whether the view is a menu, from `in_menu`.

    Returns
    -------
    bool
        True while `cycle_held`, for a view that is not a menu, the sleep
        screen or a notice.
    """
    if menu or isinstance(view, SleepView | NoticeView):
        return False
    return cycle_held(state=state)


def clock_held(*, state: State, view: ScreenView, menu: bool) -> bool:
    """
    Say whether a changed view waits for the clock to be synchronised.

    Parameters
    ----------
    state : State
        State after the event.
    view : ScreenView
        The view to draw.
    menu : bool
        Whether the view is a menu, from `in_menu`.

    Returns
    -------
    bool
        True while `clock_waiting`, for a view that is not a menu or a
        notice and does not answer a person's action.
    """
    if menu or state.cycle.free or isinstance(view, NoticeView):
        return False
    return clock_waiting(state=state)


def rendered(*, state: State, stop_view: StopView) -> ScreenView | None:
    """
    Build transit's live view, or None to keep the frame on screen.

    The first frame waits up to `LOADING_WAIT` for departures to load,
    so a quick start draws once. A view is not drawn while `cycle_held`,
    unless it is the chooser or a notice, nor while `clock_held`, nor
    when the event left it for the next frame.

    Parameters
    ----------
    state : State
        State after the event.
    stop_view : StopView
        The state's Stop view, from `banner_live_view`.

    Returns
    -------
    ScreenView | None
        The view, or None while it is held.
    """
    if state.held or loading_held(state=state):
        return None
    view = current_view(state=state, stop_view=stop_view)
    menu = state.chooser is not None
    if frame_held(state=state, view=view, menu=menu) or clock_held(
        state=state, view=view, menu=menu,
    ):
        return None
    return view


def slept_view(*, state: State, stop_view: StopView) -> SleepView | None:
    """
    Build the sleep screen, or None to keep the frame on screen.

    It is drawn once, and once more when it was drawn before departures
    loaded; like any view it waits for the first frame's load and the
    clock.

    Parameters
    ----------
    state : State
        State, asleep.
    stop_view : StopView
        The state's Stop view, from `banner_live_view`.

    Returns
    -------
    SleepView | None
        The sleep screen, or None while it is held.
    """
    last = state.last_view
    if isinstance(last, SleepView) and not last.loading:
        return None
    if state.held or loading_held(state=state):
        return None
    view = sleep_view(state=state, stop_view=stop_view)
    if clock_held(state=state, view=view, menu=False):
        return None
    return view


def scrolled(*, state: State, stop_view: StopView, draw: bool) -> State:
    """
    Keep the Stop view's scroll to the rows it shows.

    Parameters
    ----------
    state : State
        State after the event.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.
    draw : bool
        False when the event leaves the view for the next frame.

    Returns
    -------
    State
        State with the scroll of the Stop view it would draw, when the
        Stop view is shown; unchanged otherwise.
    """
    panel = state.panel
    overlay = (panel.confirm, panel.wifi, panel.menu) != (None, None, None)
    if (
        not draw
        or overlay
        or panel.asleep
        or state.view is not LiveView.STOP
        or loading_held(state=state)
    ):
        return state
    view = current_view(state=state, stop_view=stop_view)
    if isinstance(view, StopView) and view.scroll != state.scroll:
        return replace(state, scroll=view.scroll)
    return state


def settled(
    *, state: State, effects: tuple[Effect, ...] = (), draw: bool = True
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the reaches, map data and feeds needed.

    The Stop view is built once, the folds the strip on screen shows are
    opened, the Strip's scroll is clamped to its reach and a picked train
    no longer in its panel is dropped first; before that the Alerts
    view falls back to Stop when its list empties. The kit draws the
    view afterwards.

    Parameters
    ----------
    state : State
        State after the event.
    effects : tuple[Effect, ...]
        Effects so far.
    draw : bool
        False to leave the view for the next frame to draw.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        New state, marked held when `draw` is False, and effects.
    """
    state = alerts_left(state=state)
    stop_view = live_stop_view(state=state)
    banner = stop_view.banner is not None
    state = kept_selection(
        state=with_folds(state=state, banner=banner), stop_view=stop_view,
    )
    state, strips = request_strips(
        state=clamped(state=state, banner=banner),
    )
    state, requests, ask = map_effects(state=state, stop_view=stop_view)
    state, fetches = cycle_effects(state=state)
    state, loads = pattern_effects(state=state, ask=ask)
    state = scrolled(state=state, stop_view=stop_view, draw=draw)
    if not draw:
        state = replace(state, held=True)
    return state, (*effects, *strips, *requests, *fetches, *loads)
