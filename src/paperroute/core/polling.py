"""Waking the screen to start a poll cycle at once."""

from paperroute.core.cycle import cycle_effects
from paperroute.core.menu import on_asked
from paperroute.core.messages import ConfirmAsked, Effect, KnobEvent, KnobsHeld
from paperroute.core.sleep import awake
from paperroute.core.state_model import State
from paperroute.core.state_views import settled


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
