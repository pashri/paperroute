"""What the knobs do on the Live views."""

from dataclasses import replace

from paperroute.core.map_state import zoomed
from paperroute.core.messages import Knob, KnobHeld, KnobPressed, KnobTurned
from paperroute.core.places import flipped, toggled
from paperroute.core.saved import LIVE_VIEWS, LiveView
from paperroute.core.state_model import State


def switched(*, state: State) -> State:
    """
    Move to the next Live view.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State showing the view after the current one in `LIVE_VIEWS`.
    """
    index = LIVE_VIEWS.index(state.view)
    return replace(state, view=LIVE_VIEWS[(index + 1) % len(LIVE_VIEWS)])


def bottom_turned(*, state: State, steps: int) -> State:
    """
    Scroll the Stop view or the Strip, or zoom a map.

    Parameters
    ----------
    state : State
        State.
    steps : int
        Detents; clockwise (positive) scrolls down or zooms in.

    Returns
    -------
    State
        State scrolled on the Stop view or the Strip, each its own
        scroll, or zoomed on the Map and Strip + map. The Strip's scroll
        is clamped when the view is next built.
    """
    match state.view:
        case LiveView.STOP:
            return replace(state, scroll=max(0, state.scroll + steps))
        case LiveView.STRIP:
            strip = replace(state.strip, scroll=state.strip.scroll + steps)
            return replace(state, strip=strip)
    return replace(state, map=zoomed(map_state=state.map, steps=steps))


def knob_moved(
    *, state: State, event: KnobTurned | KnobPressed | KnobHeld
) -> State:
    """
    Apply a knob event on a Live view.

    The top knob turns between pins; the bottom knob scrolls or zooms,
    its press moves to the next Live view and its hold pins or unpins.
    The top press opens the chooser, which is not built yet, and the top
    hold is kept for settings.

    Parameters
    ----------
    state : State
        State.
    event : KnobTurned | KnobPressed | KnobHeld
        Knob event.

    Returns
    -------
    State
        New state; the same object when the event does nothing.
    """
    match event:
        case KnobTurned(knob=Knob.TOP, steps=steps):
            return flipped(state=state, steps=steps)
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            return bottom_turned(state=state, steps=steps)
        case KnobPressed(knob=Knob.BOTTOM):
            return switched(state=state)
        case KnobHeld(knob=Knob.BOTTOM):
            return toggled(state=state)
    return state
