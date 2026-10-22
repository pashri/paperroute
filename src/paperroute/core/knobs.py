"""What the knobs do on the Live views."""

from dataclasses import replace

from paperroute.core.map_state import zoomed
from paperroute.core.messages import (
    Knob,
    KnobEvent,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
)
from paperroute.core.places import flipped, toggled
from paperroute.core.saved import LIVE_VIEWS, LiveView
from paperroute.core.state_model import State
from paperroute.core.state_views import live_stop_view
from paperroute.core.strip_select import selected_turned, unselected


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
        stays within the rows last shown.
    """
    match state.view:
        case LiveView.STOP:
            return replace(state, scroll=max(0, state.scroll + steps))
        case LiveView.STRIP:
            scroll = state.strip.scroll + steps
            if state.strip.bounds is not None:
                low, high = state.strip.bounds
                scroll = min(max(scroll, low), high)
            strip = replace(state.strip, scroll=scroll, asked=scroll)
            return replace(state, strip=strip)
    return replace(state, map=zoomed(map_state=state.map, steps=steps))


def moved_off(*, before: State, after: State) -> State:
    """
    Drop the Strip's picked train when the pin or view changed.

    Parameters
    ----------
    before : State
        State before the knob event.
    after : State
        State after it.

    Returns
    -------
    State
        `after`, with no train picked unless it is `before`.
    """
    return before if after is before else unselected(state=after)


def knob_moved(*, state: State, event: KnobEvent) -> State:
    """
    Apply a knob event on a Live view.

    The top knob turns between pins; the bottom knob scrolls or zooms,
    its press moves to the next Live view, its hold pins or unpins and a
    turn while held picks a train in the Strip's panel. Changing the pin
    or the view drops the pick. The top press, which opens the chooser,
    is handled with the chooser's knobs. The top hold does nothing, and
    holding both knobs, which opens Settings, is a separate event.

    Parameters
    ----------
    state : State
        State.
    event : KnobEvent
        Knob event.

    Returns
    -------
    State
        New state; the same object when the event does nothing.
    """
    match event:
        case KnobTurned(knob=Knob.TOP, steps=steps):
            after = flipped(state=state, steps=steps)
            return moved_off(before=state, after=after)
        case KnobTurned(knob=Knob.BOTTOM, steps=steps):
            return bottom_turned(state=state, steps=steps)
        case KnobPressed(knob=Knob.BOTTOM):
            return moved_off(before=state, after=switched(state=state))
        case KnobHeld(knob=Knob.BOTTOM):
            return moved_off(before=state, after=toggled(state=state))
        case KnobHeldTurned(knob=Knob.BOTTOM, steps=steps):
            stop_view = live_stop_view(state=state)
            return selected_turned(
                state=state, stop_view=stop_view, steps=steps,
            )
    return state
