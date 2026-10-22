"""Sleeping the screen: the state it leaves and the screen it shows."""

from dataclasses import replace
from typing import Final

from paperroute.core.map_requests import map_inputs
from paperroute.core.map_view import build_map_view
from paperroute.core.panel_views import SleepView, sleep_map
from paperroute.core.state_model import State
from paperroute.core.stop_view import DOT, StopView

SLEEP_BAND: Final[str] = f'Asleep{DOT}turn or press a knob to wake'


def slept(*, state: State) -> State:
    """
    Put the screen to sleep.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with the screen asleep and the menu closed.
    """
    panel = replace(state.panel, asleep=True, menu=None)
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
        State awake, with every feed due at once.
    """
    panel = replace(state.panel, asleep=False)
    map_state = replace(state.map, fetched_at={})
    return replace(state, panel=panel, map=map_state, last_fetch=None)


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
        under the sleep band. It does not change as time passes.
    """
    inputs = map_inputs(state=state, stop_view=stop_view)
    return SleepView(
        map=sleep_map(view=build_map_view(inputs=inputs)), band=SLEEP_BAND,
    )
