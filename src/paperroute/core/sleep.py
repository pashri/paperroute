"""Sleeping the screen: the state it leaves and the screen it shows."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.connection import live_chains
from paperroute.core.connection_screens import connection_map
from paperroute.core.map_requests import map_inputs
from paperroute.core.map_state import awaiting
from paperroute.core.map_view import MapView, build_map_view
from paperroute.core.model import is_connection
from paperroute.core.panel_views import SleepView, sleep_map
from paperroute.core.places import shown
from paperroute.core.state_model import State, restarted
from paperroute.core.stop_view import StopView
from paperroute_device.core.text import DOT

SLEEP_BAND: Final[str] = f'Asleep{DOT}turn or press a knob to wake'
WAKE_FOR: Final[timedelta] = timedelta(minutes=5)


def slept(*, state: State, by_quiet: bool = False) -> State:
    """
    Put the screen to sleep.

    Parameters
    ----------
    state : State
        State.
    by_quiet : bool
        Whether quiet hours sleep it, so a tick can end the sleep.

    Returns
    -------
    State
        State with the screen asleep, the menu closed and no wake
        running.
    """
    panel = replace(
        state.panel,
        asleep=True,
        menu=None,
        by_quiet=by_quiet,
        awake_until=None,
    )
    return replace(state, panel=panel)


def held_awake(*, state: State) -> State:
    """
    Keep the screen awake for `WAKE_FOR` from now.

    Parameters
    ----------
    state : State
        State, awake.

    Returns
    -------
    State
        State whose wake ends `WAKE_FOR` after now.
    """
    panel = replace(state.panel, awake_until=state.now + WAKE_FOR)
    return replace(state, panel=panel)


def awake(*, state: State) -> State:
    """
    End sleep and forget when the feeds were last fetched.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State awake, with a cycle due at once.
    """
    panel = replace(state.panel, asleep=False, by_quiet=False)
    map_state = replace(state.map, fetched_at={})
    return restarted(
        state=replace(state, panel=panel, map=map_state, last_fetch=None),
    )


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
