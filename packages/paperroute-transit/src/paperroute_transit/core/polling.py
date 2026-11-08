"""Waking the screen to start a poll cycle at once."""

from paperroute_device.core.device_events import ConfirmAsked
from paperroute_hardware.core.knob_events import KnobEvent, KnobsHeld
from paperroute_transit.core.cycle import cycle_effects
from paperroute_transit.core.menu import on_asked
from paperroute_transit.core.messages import Effect
from paperroute_transit.core.sleep import awake
from paperroute_transit.core.state_model import State
from paperroute_transit.core.state_views import settled


def woken(
    *, state: State, event: KnobEvent | KnobsHeld | ConfirmAsked
) -> tuple[State, tuple[Effect, ...]]:
    """
    Wake the screen, starting a poll cycle at once.

    The touch that wakes is not acted on. A phone's ask is the
    exception: it wakes the screen and shows its confirm, in one redraw.

    Parameters
    ----------
    state : State
        State, asleep.
    event : KnobEvent | KnobsHeld | ConfirmAsked
        What woke it.

    Returns
    -------
    tuple[State, tuple[Effect, ...]]
        State awake and the cycle's fetches, with the Live view drawn,
        or the confirm when the event was an ask the panel shows.
    """
    state, polls = cycle_effects(state=awake(state=state))
    if isinstance(event, ConfirmAsked):
        asked, effects = on_asked(state=state, event=event)
        if effects:
            return asked, (*polls, *effects)
    return settled(state=state, effects=polls)
