"""Build the screen a state shows, and what it keeps between restarts."""

from dataclasses import replace

from paperroute.core.chooser_steps import chooser_view
from paperroute.core.chooser_view import ChooserView
from paperroute.core.live import LiveDeparture, live_since, resolved
from paperroute.core.map_requests import (
    map_effects,
    map_inputs,
    polled_positions,
)
from paperroute.core.map_view import STRIP_MAP_WIDTH, MapView, build_map_view
from paperroute.core.messages import Effect, Render
from paperroute.core.places import shown, shown_index
from paperroute.core.saved import LiveView, Saved
from paperroute.core.state_model import State
from paperroute.core.stop_view import (
    StopInputs,
    StopView,
    build_stop_view,
    pin_tag,
)
from paperroute.core.strip_model import StripInputs, StripMapView, StripView
from paperroute.core.strip_opening import with_folds
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
        The pins, the place shown, the Live view, the zoom and settings.
    """
    places = state.places
    return Saved(
        pins=places.pins,
        current=places.current,
        unpinned=places.unpinned,
        view=state.view,
        zoom=state.map.zoom,
        settings=state.settings,
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
