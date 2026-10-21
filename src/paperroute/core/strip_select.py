"""Pick a train in the Strip's panel, whose times the strip then shows."""

from dataclasses import replace

from paperroute.core.model import Departure
from paperroute.core.saved import LiveView
from paperroute.core.state_model import State
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import PANEL_ROWS, selected_index, trip_day


def panel_trains(*, stop_view: StopView) -> tuple[Departure, ...]:
    """
    List the departures the Strip's panel shows on a Live view.

    Parameters
    ----------
    stop_view : StopView
        The place's unscrolled Stop view.

    Returns
    -------
    tuple[Departure, ...]
        The first departures, as many as the panel has rows for under
        any banner.
    """
    shown = PANEL_ROWS - (stop_view.banner is not None)
    return stop_view.head_departures[:shown]


def unselected(*, state: State) -> State:
    """
    Go back to the next train.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State with no train picked; the same object when none was.
    """
    if state.strip.selected is None:
        return state
    return replace(state, strip=replace(state.strip, selected=None))


def selected_turned(
    *, state: State, stop_view: StopView, steps: int,
) -> State:
    """
    Move the pick through the panel's trains.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The place's unscrolled Stop view.
    steps : int
        Detents; clockwise (positive) moves down the panel.

    Returns
    -------
    State
        On the Strip, the pick moved, held at the last row, and back to
        no pick (the next train) when turned back past the first row,
        which is the first pick; else the same object.
    """
    trains = panel_trains(stop_view=stop_view)
    if state.view is not LiveView.STRIP or not trains:
        return state
    index = selected_index(selected=state.strip.selected, trains=trains)
    after = (-1 if index is None else index) + steps
    if after < 0:
        return unselected(state=state)
    picked = trip_day(departure=trains[min(after, len(trains) - 1)])
    return replace(state, strip=replace(state.strip, selected=picked))


def kept_selection(*, state: State, stop_view: StopView) -> State:
    """
    Drop the pick once it no longer applies.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The place's unscrolled Stop view.

    Returns
    -------
    State
        State as it is while the picked train is in the panel of the
        Strip on screen; else with no pick, as when the train has left
        the From.
    """
    index = selected_index(
        selected=state.strip.selected,
        trains=panel_trains(stop_view=stop_view),
    )
    on_strip = state.view is LiveView.STRIP and state.chooser is None
    if on_strip and index is not None:
        return state
    return unselected(state=state)
