"""Build the Live views of a connection pin."""

from dataclasses import replace

from paperroute.core.chains import Chain
from paperroute.core.connection import live_chains
from paperroute.core.connection_map import build_connection_map, change_panes
from paperroute.core.connection_strip import (
    ConnectionStripMapView,
    ConnectionStripView,
    build_connection_strip,
)
from paperroute.core.connection_view import (
    ConnectionView,
    build_connection_view,
)
from paperroute.core.map_requests import pin_map_inputs, polled_positions
from paperroute.core.map_view import STRIP_MAP_WIDTH, MapView
from paperroute.core.places import shown
from paperroute.core.rhythm import frame_time, stale_after
from paperroute.core.saved import ConnectionMap, LiveView
from paperroute.core.state_model import State
from paperroute.core.stop_view import StopView
from paperroute_transit.core.model import Pin, is_connection

type ConnectionScreen = (
    ConnectionView | MapView | ConnectionStripView | ConnectionStripMapView
)


def connection_map(
    *, state: State, stop_view: StopView, chains: tuple[Chain, ...],
    width: int | None = None, asleep: bool = False,
) -> MapView:
    """
    Build the connection map the state shows.

    Parameters
    ----------
    state : State
        State showing a connection pin.
    stop_view : StopView
        The direct list's Stop view.
    chains : tuple[Chain, ...]
        The workable chains.
    width : int | None
        Map width in pixels, or None for the Map's own.
    asleep : bool
        True to fit the map clear of the sleep band.

    Returns
    -------
    MapView
        The map, framed by the connection map setting: on the Map, a
        two-change hero in the each-change setting gives one pane per
        change side by side.
    """
    inputs = replace(
        pin_map_inputs(state=state, stop_view=stop_view), asleep=asleep,
    )
    mode = state.settings.board.connection_map
    if width is not None:
        inputs = replace(inputs, width=width)
    elif chains and mode is ConnectionMap.CHANGES:
        panes = change_panes(
            inputs=inputs, pin=shown(state=state), chain=chains[0], mode=mode,
        )
        if len(panes) > 1:
            return replace(panes[0], panes=panes[1:])
    return build_connection_map(
        inputs=inputs,
        pin=shown(state=state),
        chains=chains,
        mode=mode,
    )


def connection_strip(
    *, state: State, stop_view: StopView, pin: Pin, chains: tuple[Chain, ...],
) -> ConnectionStripView:
    """
    Build the connection Strip the state shows.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The direct list's Stop view.
    pin : Pin
        Connection pin.
    chains : tuple[Chain, ...]
        The workable chains.

    Returns
    -------
    ConnectionStripView
        The sections of the chain the scroll selects.
    """
    return build_connection_strip(
        stop_view=stop_view,
        pin=pin,
        chains=chains,
        selected=state.scroll,
        patterns=state.map.patterns,
        positions=polled_positions(state=state),
        now=frame_time(state=state),
        stale_after=stale_after(state=state),
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
        The direct list's Stop view, from `live_stop_view`.

    Returns
    -------
    ConnectionScreen | None
        The connection Stop, Map, Strip or Strip + map view; None for a
        plain pin or on the Alerts view.
    """
    pin = shown(state=state)
    if not is_connection(pin=pin) or state.view is LiveView.ALERTS:
        return None
    chains = live_chains(state=state)
    match state.view:
        case LiveView.MAP:
            return connection_map(
                state=state, stop_view=stop_view, chains=chains,
            )
        case LiveView.STRIP:
            return connection_strip(
                state=state, stop_view=stop_view, pin=pin, chains=chains,
            )
        case LiveView.STRIP_MAP:
            return ConnectionStripMapView(
                map=connection_map(
                    state=state,
                    stop_view=stop_view,
                    chains=chains,
                    width=STRIP_MAP_WIDTH,
                ),
                pane=connection_strip(
                    state=state, stop_view=stop_view, pin=pin, chains=chains,
                ),
            )
    return build_connection_view(
        stop_view=stop_view,
        pin=pin,
        chains=chains,
        scroll=state.scroll,
        now=frame_time(state=state),
    )
