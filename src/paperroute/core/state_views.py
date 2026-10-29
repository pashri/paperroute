"""Build the screen a state shows, and what it keeps between restarts."""

from dataclasses import replace

from paperroute.core.alert_match import alert_banner
from paperroute.core.alert_state import alerts_left, shown_alerts
from paperroute.core.alerts_view import build_alerts_view
from paperroute.core.auto_pins import rule_holds
from paperroute.core.banners import device_banner
from paperroute.core.chooser_steps import chooser_view
from paperroute.core.connection_screens import connection_screen
from paperroute.core.live import LiveDeparture, live_since, resolved
from paperroute.core.map_requests import (
    map_effects,
    map_inputs,
    polled_positions,
)
from paperroute.core.map_view import STRIP_MAP_WIDTH, build_map_view
from paperroute.core.messages import Effect, NoticeView, Render, ScreenView
from paperroute.core.panel_screens import panel_view
from paperroute.core.panel_views import PanelView, SleepView
from paperroute.core.places import shown, shown_index
from paperroute.core.rebuild import building_view
from paperroute.core.refresh import needs_greys
from paperroute.core.saved import LiveView, Saved
from paperroute.core.sleep import sleep_view
from paperroute.core.state_model import State
from paperroute.core.stop_view import (
    StopInputs,
    StopView,
    build_stop_view,
    pin_tag,
)
from paperroute.core.strip_model import StripInputs, StripMapView
from paperroute.core.strip_opening import with_folds
from paperroute.core.strip_select import kept_selection
from paperroute.core.strip_state import clamped, request_strips, strip_inputs
from paperroute.core.strip_view import build_strip_pane, build_strip_view


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
            notice=device_banner(state=state),
            alert=alert_banner(
                listed=shown_alerts(state=state), now=state.now,
            ),
            large=state.settings.larger_text,
            inverted=state.settings.inverted_lists,
            tag=pin_tag(
                index=shown_index(state=state),
                total=len(state.places.pins),
                nickname=pin.nickname,
                auto=rule_holds(state=state),
            ),
        ),
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
) -> ScreenView:
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
    ScreenView
        The confirm or open menu's screen, else the sleep screen, else
        the Building screen with no timetable, else the open chooser's
        screen, else the Live view shown.
    """
    overlay: PanelView | SleepView | NoticeView | None = panel_view(
        state=state,
    )
    if overlay is None and state.panel.asleep:
        overlay = sleep_view(state=state, stop_view=stop_view)
    if overlay is None and state.rebuild.missing:
        overlay = building_view(state=state)
    if overlay is not None:
        return overlay
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
            inputs = map_inputs(state=state, stop_view=stop_view)
            strip = shown_strip(state=state, stop_view=stop_view)
            return StripMapView(
                map=build_map_view(
                    inputs=replace(inputs, width=STRIP_MAP_WIDTH),
                ),
                pane=build_strip_pane(inputs=strip),
            )
        case LiveView.ALERTS if listed := shown_alerts(state=state):
            return build_alerts_view(
                listed=listed,
                selected=state.alerts.selected,
                page=state.alerts.page,
                stop_view=stop_view,
                now=state.now,
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

    The first frame, the sleep screen, the first frame after it and a
    change into or out of a view with grey tiles are full refreshes.
    While the sleep screen is shown nothing redraws; it is drawn once,
    and once more when it was drawn before departures loaded.

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
    last = state.last_view
    if state.panel.asleep and isinstance(last, SleepView) and not last.loading:
        return state, effects
    view = current_view(state=state, stop_view=stop_view)
    if isinstance(view, StopView) and state.view is LiveView.STOP:
        state = replace(state, scroll=view.scroll)
    if view == state.last_view:
        return state, effects
    full = (
        state.last_view is None
        or isinstance(view, SleepView)
        or isinstance(state.last_view, SleepView)
        or needs_greys(view=view) != needs_greys(view=state.last_view)
    )
    render = Render(view=view, full=full)
    return replace(state, last_view=view), (*effects, render)


def settled(
    *, state: State, effects: tuple[Effect, ...] = ()
) -> tuple[State, tuple[Effect, ...]]:
    """
    Ask for the reaches and map data needed, and redraw.

    The Stop view is built once, the folds the strip on screen shows are
    opened, the Strip's scroll is clamped to its reach and a picked train
    no longer in its panel is dropped first; before that the Alerts
    view falls back to Stop when its list empties.

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
    state = alerts_left(state=state)
    stop_view = live_stop_view(state=state)
    banner = stop_view.banner is not None
    state = kept_selection(
        state=with_folds(state=state, banner=banner), stop_view=stop_view,
    )
    state, strips = request_strips(
        state=clamped(state=state, banner=banner),
    )
    state, requests = map_effects(state=state, stop_view=stop_view)
    return rendered(
        state=state,
        stop_view=stop_view,
        effects=(*effects, *strips, *requests),
    )
