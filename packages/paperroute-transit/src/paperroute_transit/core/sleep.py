"""The sleep screen transit shows."""

from paperroute_device.core.sleep import SLEEP_BAND
from paperroute_transit.core.connection import live_chains
from paperroute_transit.core.connection_screens import connection_map
from paperroute_transit.core.map_requests import map_inputs
from paperroute_transit.core.map_state import awaiting
from paperroute_transit.core.map_view import MapView, build_map_view
from paperroute_transit.core.model import is_connection
from paperroute_transit.core.panel_views import SleepView, sleep_map
from paperroute_transit.core.places import shown
from paperroute_transit.core.state_model import State
from paperroute_transit.core.stop_view import StopView


def sleep_view(*, state: State, stop_view: StopView) -> SleepView:
    """
    Build the sleep screen.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view, from `live_stop_view`.

    Returns
    -------
    SleepView
        The shown pin's map with no vehicles, sidebar, banner or clock,
        under the sleep band, marked loading until departures load and
        until the shapes and patterns asked for arrive. It does not
        change as time passes.
    """
    return SleepView(
        map=sleep_map(view=pin_map(state=state, stop_view=stop_view)),
        band=SLEEP_BAND,
        loading=(
            state.board.loaded_until is None
            or awaiting(map_state=state.map)
        ),
    )


def pin_map(*, state: State, stop_view: StopView) -> MapView:
    """
    Build the shown pin's Map view as the Map would show it.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The state's Stop view.

    Returns
    -------
    MapView
        A connection pin's connection map, which draws its next
        connection or says there is none; any other pin's map. Each is
        fitted clear of the sleep band.
    """
    if is_connection(pin=shown(state=state)):
        return connection_map(
            state=state,
            stop_view=stop_view,
            chains=live_chains(state=state),
            asleep=True,
        )
    return build_map_view(
        inputs=map_inputs(state=state, stop_view=stop_view, asleep=True),
    )
