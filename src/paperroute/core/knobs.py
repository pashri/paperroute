"""What the knobs do on the Live views."""

from dataclasses import replace

from paperroute.core.alert_state import has_alerts
from paperroute.core.connection import live_chains
from paperroute.core.map_state import zoomed
from paperroute.core.model import is_connection
from paperroute.core.places import flipped, shown, toggled
from paperroute.core.saved import LIVE_VIEWS, LiveView
from paperroute.core.state_model import State
from paperroute.core.state_views import live_stop_view
from paperroute.core.strip_select import selected_turned, unselected
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobEvent,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
)


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
        State showing the view after the current one in `LIVE_VIEWS`;
        after Strip + map, the Alerts view while the pin has alerts
        listed, and the Stop view after the Alerts view.
    """
    match state.view:
        case LiveView.STRIP_MAP if has_alerts(state=state):
            return replace(state, view=LiveView.ALERTS)
        case LiveView.ALERTS:
            return replace(state, view=LiveView.STOP)
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
        stays within the rows last shown; a connection pin's Strip turns
        the pin's scroll, which picks the pair. Unchanged on the Alerts
        view.
    """
    connection = is_connection(pin=shown(state=state))
    match state.view:
        case LiveView.ALERTS:
            return state
        case LiveView.STOP | LiveView.STRIP if connection:
            return pair_scrolled(state=state, steps=steps)
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


def pair_scrolled(*, state: State, steps: int) -> State:
    """
    Scroll a connection pin's pairs.

    Parameters
    ----------
    state : State
        State showing a connection pin on the Stop view or the Strip.
    steps : int
        Detents; clockwise (positive) scrolls down.

    Returns
    -------
    State
        State whose scroll stays between 0 and the last chain the view
        can start at: the last chain on the Strip, the one before it on
        the Stop view, whose list starts after the hero.
    """
    chains = len(live_chains(state=state))
    last = chains - (2 if state.view is LiveView.STOP else 1)
    scroll = min(state.scroll + steps, last)
    return replace(state, scroll=max(0, scroll))


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
