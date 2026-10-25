"""The auto pin rules: show a pin in its windows, then go back."""

from dataclasses import replace

from paperroute.core.auto_model import AutoState, Before, Occurrence, running
from paperroute.core.messages import Knob, KnobEvent, KnobHeld, KnobTurned
from paperroute.core.model import pin_ref, same_place
from paperroute.core.places import committed, pin_index, shown, with_place
from paperroute.core.sleep import awake
from paperroute.core.state_model import State


def clock_trusted(*, state: State) -> bool:
    """
    Say whether the clock can be trusted for the rules.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True once device facts are known and the clock is not known to
        be unsynced.
    """
    facts = state.panel.facts
    return facts is not None and facts.clock_synced is not False


def blocked(*, state: State) -> bool:
    """
    Say whether something open makes the rules wait.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True while the chooser, the Settings menu, Wi-Fi setup or a
        confirm is open, or a power action or reset is under way.
    """
    panel = state.panel
    if state.power is not None or state.chooser is not None:
        return True
    return (panel.confirm, panel.menu, panel.wifi, panel.resetting) != (
        None, None, None, None,
    )


def selected(*, state: State, index: int) -> State:
    """
    Show a pin, unless it is already shown.

    Parameters
    ----------
    state : State
        State.
    index : int
        The pin's index.

    Returns
    -------
    State
        State showing the pin; the same object when it was shown.
    """
    places = state.places
    if places.unpinned is None and places.current == index:
        return state
    state = replace(state, places=replace(places, current=index))
    return with_place(state=state, pin=None)


def before_of(*, state: State) -> Before:
    """
    Find the place to go back to when a window starts.

    Parameters
    ----------
    state : State
        State before the start.

    Returns
    -------
    Before
        The recorded place in a chain, or else the place shown, without
        its windows.
    """
    auto = state.auto
    if auto.occurrence is not None and auto.before is not None:
        return auto.before
    return Before(
        place=replace(shown(state=state), windows=()),
        pinned=state.places.unpinned is None,
    )


def started(*, state: State, occurrence: Occurrence) -> State:
    """
    Apply an occurrence's start.

    Parameters
    ----------
    state : State
        State.
    occurrence : Occurrence
        The running occurrence.

    Returns
    -------
    State
        State showing the occurrence's pin, with it recorded and a Sleep
        now sleep ended.
    """
    before = before_of(state=state)
    index = next(
        (
            index
            for index, pin in enumerate(state.places.pins)
            if pin_ref(pin=pin) == occurrence.ref
        ),
        None,
    )
    if index is not None:
        state = selected(state=state, index=index)
    if state.panel.asleep and not state.panel.by_quiet:
        state = awake(state=state)
    auto = replace(state.auto, occurrence=occurrence, before=before)
    return replace(state, auto=auto)


def returned(*, state: State, before: Before) -> State:
    """
    Show the place from before a window.

    Parameters
    ----------
    state : State
        State.
    before : Before
        The place.

    Returns
    -------
    State
        State showing it; unchanged when it was a pin no longer pinned.
    """
    if not before.pinned:
        return committed(state=state, pin=before.place)
    index = pin_index(places=state.places, pin=before.place)
    return state if index is None else selected(state=state, index=index)


def ended(*, state: State) -> State:
    """
    Apply the end of the recorded occurrence.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State back on the place from before, unless overridden, with no
        auto state.
    """
    auto = state.auto
    if auto.overridden_at is None and auto.before is not None:
        state = returned(state=state, before=auto.before)
    return replace(state, auto=AutoState())


def applied(*, state: State, found: Occurrence) -> State:
    """
    Handle an occurrence running that is not the one recorded.

    Parameters
    ----------
    state : State
        State.
    found : Occurrence
        The running occurrence.

    Returns
    -------
    State
        State with it recorded but not shown when it started before an
        override; unchanged while blocked; otherwise started.
    """
    overridden_at = state.auto.overridden_at
    if overridden_at is not None and found.start <= overridden_at:
        return replace(state, auto=replace(state.auto, occurrence=found))
    if blocked(state=state):
        return state
    return started(state=state, occurrence=found)


def auto_step(*, state: State) -> State:
    """
    Run the auto pin rules at the state's time.

    Parameters
    ----------
    state : State
        State at the tick's time.

    Returns
    -------
    State
        State with a start or end applied; unchanged while the clock is
        untrusted.
    """
    if not clock_trusted(state=state):
        return state
    found = running(pins=state.places.pins, now=state.now)
    if found is None:
        if state.auto.occurrence is None or blocked(state=state):
            return state
        return ended(state=state)
    if found == state.auto.occurrence:
        return state
    return applied(state=state, found=found)


def turned_away(*, before: State, after: State, event: KnobEvent) -> bool:
    """
    Say whether a top turn changed the place shown.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after it.
    event : KnobEvent
        Knob event.

    Returns
    -------
    bool
        True for a top turn outside the chooser that shows another place.
    """
    if not isinstance(event, KnobTurned) or event.knob is not Knob.TOP:
        return False
    return before.chooser is None and not same_place(
        first=shown(state=before), second=shown(state=after),
    )


def unpinned_auto(*, before: State, after: State, event: KnobEvent) -> bool:
    """
    Say whether a bottom hold unpinned the auto pin.

    Parameters
    ----------
    before : State
        State before the event.
    after : State
        State after it.
    event : KnobEvent
        Knob event.

    Returns
    -------
    bool
        True when a bottom hold outside the chooser unpinned the shown
        pin of the recorded occurrence.
    """
    occurrence = before.auto.occurrence
    if event != KnobHeld(knob=Knob.BOTTOM) or occurrence is None:
        return False
    if before.chooser is not None or before.places.unpinned is not None:
        return False
    return after.places.unpinned is not None and pin_ref(
        pin=shown(state=before),
    ) == occurrence.ref


def with_override(
    *, before: State, after: State, event: KnobEvent,
) -> State:
    """
    Record an override of the auto pin.

    Parameters
    ----------
    before : State
        State before the knob event.
    after : State
        State after it.
    event : KnobEvent
        Knob event.

    Returns
    -------
    State
        `after` with `overridden_at` set to now when an occurrence is
        recorded and the event turned away from it or unpinned it;
        otherwise `after` itself.
    """
    if after.auto.occurrence is None:
        return after
    moved = turned_away(before=before, after=after, event=event)
    if not moved and not unpinned_auto(
        before=before, after=after, event=event,
    ):
        return after
    auto = replace(after.auto, overridden_at=after.now)
    return replace(after, auto=auto)


def rule_holds(*, state: State) -> bool:
    """
    Say whether a rule is showing its pin now.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    bool
        True while an occurrence is recorded and not overridden, and its
        pin is shown pinned.
    """
    auto = state.auto
    if auto.occurrence is None or auto.overridden_at is not None:
        return False
    return state.places.unpinned is None and pin_ref(
        pin=shown(state=state),
    ) == auto.occurrence.ref
