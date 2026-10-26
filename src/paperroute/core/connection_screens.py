"""Build the Live views of a connection pin."""

from dataclasses import replace

from paperroute.core.connection import live_pairs
from paperroute.core.connection_map import build_connection_map
from paperroute.core.connection_model import Pair
from paperroute.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
    build_connection_strip,
)
from paperroute.core.connection_view import (
    ConnectionView,
    build_connection_view,
)
from paperroute.core.map_requests import map_inputs
from paperroute.core.map_view import STRIP_MAP_WIDTH, MapView
from paperroute.core.model import Pin
from paperroute.core.places import shown
from paperroute.core.saved import LiveView
from paperroute.core.state_model import State
from paperroute.core.stop_view import StopView

type ConnectionScreen = (
    ConnectionView | MapView | ConnectionStripView | ConnectionStripMapView
)


def connection_map(
    *, state: State, stop_view: StopView, pairs: tuple[Pair, ...],
    width: int | None = None,
) -> MapView:
    """
    Build the connection map the state shows.

    Parameters
    ----------
    state : State
        State showing a connection pin.
    stop_view : StopView
        Leg 1's Stop view.
    pairs : tuple[Pair, ...]
        The workable pairs.
    width : int | None
        Map width in pixels, or None for the Map's own.

    Returns
    -------
    MapView
        The map, framed by the connection map setting.
    """
    inputs = map_inputs(state=state, stop_view=stop_view)
    if width is not None:
        inputs = replace(inputs, width=width)
    return build_connection_map(
        inputs=inputs,
        pin=shown(state=state),
        pairs=pairs,
        mode=state.settings.connection_map,
    )


def connection_strip(
    *, state: State, stop_view: StopView, pin: Pin, pairs: tuple[Pair, ...],
) -> ConnectionStripView:
    """
    Build the connection Strip the state shows.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        Leg 1's Stop view.
    pin : Pin
        Connection pin.
    pairs : tuple[Pair, ...]
        The workable pairs.

    Returns
    -------
    ConnectionStripView
        The sections of the pair the scroll selects.
    """
    return build_connection_strip(
        stop_view=stop_view,
        pin=pin,
        pairs=pairs,
        selected=state.scroll,
        patterns=state.map.patterns,
    )


def connection_screen(
    *, state: State, stop_view: StopView
) -> ConnectionScreen | None:
    """
    Build the Live view of the shown connection pin.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        Leg 1's Stop view, from `live_stop_view`.

    Returns
    -------
    ConnectionScreen | None
        The connection Stop, Map, Strip or Strip + map view; None for a
        plain pin or on the Alerts view.
    """
    pin = shown(state=state)
    if pin.change is None or state.view is LiveView.ALERTS:
        return None
    pairs = live_pairs(state=state)
    match state.view:
        case LiveView.MAP:
            return connection_map(
                state=state, stop_view=stop_view, pairs=pairs,
            )
        case LiveView.STRIP:
            return connection_strip(
                state=state, stop_view=stop_view, pin=pin, pairs=pairs,
            )
        case LiveView.STRIP_MAP:
            return ConnectionStripMapView(
                map=connection_map(
                    state=state,
                    stop_view=stop_view,
                    pairs=pairs,
                    width=STRIP_MAP_WIDTH,
                ),
                pane=connection_strip(
                    state=state, stop_view=stop_view, pin=pin, pairs=pairs,
                ),
            )
    return build_connection_view(
        stop_view=stop_view,
        pin=pin,
        pairs=pairs,
        scroll=state.scroll,
        now=state.now,
    )
