"""How a plain pin's map is framed when it opens."""

from paperroute_transit.core.day_lines import line_choice
from paperroute_transit.core.map_fit import (
    line_frame,
    line_framed,
    steady_frame,
)
from paperroute_transit.core.map_view import MapInputs
from paperroute_transit.core.places import shown
from paperroute_transit.core.state_model import State
from paperroute_transit.core.stop_view import StopView


def opens_by_line(*, state: State) -> bool:
    """
    Say whether the shown pin's kind is framed by its line.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True when the shown pin's kind is chosen by line in the city.
    """
    return line_framed(kind=shown(state=state).kind, modes=state.city.modes)


def plain_frame(
    *, state: State, stop_view: StopView, inputs: MapInputs,
) -> MapInputs:
    """
    Frame a plain pin's map by its kind, then the map framing setting.

    Parameters
    ----------
    state : State
        State.
    stop_view : StopView
        The pin's Stop view.
    inputs : MapInputs
        The map's inputs, centred on the From.

    Returns
    -------
    MapInputs
        `line_frame` for a kind chosen by line, whatever the setting;
        else `steady_frame` with the lines the pin draws.
    """
    if opens_by_line(state=state):
        return line_frame(inputs=inputs)
    return steady_frame(
        inputs=inputs,
        framing=state.settings.board.map_framing,
        held=state.map.frame,
        key=state.map.frame_key,
        choice=line_choice(state=state, stop_view=stop_view),
    )
