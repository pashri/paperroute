"""Adding, changing, deleting and moving pins."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute.core.messages import (
    PinDeleted,
    PinEdit,
    PinMoved,
    PinSaved,
    PinsOrdered,
)
from paperroute.core.model import Pin, pin_ref, same_place
from paperroute.core.pin_choices import clean_nickname
from paperroute.core.places import pin_index, with_place, with_unpinned
from paperroute.core.state_model import Places, State


def ref_index(*, places: Places, ref: str) -> int | None:
    """
    Find a pin by its ref.

    Parameters
    ----------
    places : Places
        The pins.
    ref : str
        A pin ref.

    Returns
    -------
    int | None
        The pin's index, or None when no pin has the ref.
    """
    return next(
        (
            index
            for index, pin in enumerate(places.pins)
            if pin_ref(pin=pin) == ref
        ),
        None,
    )


def reordered(*, places: Places, order: Sequence[int]) -> Places:
    """
    Put the pins in a new order.

    Parameters
    ----------
    places : Places
        The pins.
    order : Sequence[int]
        The old index of each pin, in the new order.

    Returns
    -------
    Places
        The pins in the new order; the current pin is still the same
        pin.
    """
    return replace(
        places,
        pins=tuple(places.pins[index] for index in order),
        current=order.index(places.current),
    )


def tidied(*, pin: Pin) -> Pin:
    """
    Clean a pin's nickname and sort its route filter.

    Parameters
    ----------
    pin : Pin
        The pin as typed.

    Returns
    -------
    Pin
        The pin with a tidy nickname and each filter route once, sorted.
    """
    nickname = None if pin.nickname is None else clean_nickname(
        text=pin.nickname
    )
    return replace(
        pin, nickname=nickname, only=tuple(sorted(set(pin.only)))
    )


def placed(
    *, pins: tuple[Pin, ...], index: int | None, pin: Pin
) -> tuple[Pin, ...]:
    """
    Add a pin last, or put it in place of another.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        The pins.
    index : int | None
        The index to replace, or None to add.
    pin : Pin
        The pin to keep.

    Returns
    -------
    tuple[Pin, ...]
        The new pins.
    """
    if index is None:
        return (*pins, pin)
    return (*pins[:index], pin, *pins[index + 1:])


def with_saved(*, state: State, ref: str | None, pin: Pin) -> State:
    """
    Add a pin, or replace the pin with a ref.

    Parameters
    ----------
    state : State
        State.
    ref : str | None
        The ref of the pin to replace, or None to add.
    pin : Pin
        The pin to keep.

    Returns
    -------
    State
        State with the pin saved. A new pin goes last and is not shown.
        Unchanged when the ref is unknown or the pin would be the same
        place as another pin. Showing a pin whose place changed starts
        its board afresh.
    """
    places = state.places
    pin = tidied(pin=pin)
    index = None if ref is None else ref_index(places=places, ref=ref)
    taken = pin_index(places=places, pin=pin)
    if (ref is not None and index is None) or taken not in (None, index):
        return state
    pins = placed(pins=places.pins, index=index, pin=pin)
    if pins == places.pins:
        return state
    after = replace(state, places=replace(places, pins=pins))
    if index == places.current and places.unpinned is None:
        if not same_place(first=pin, second=places.pins[index]):
            return with_place(state=after, pin=None)
    return after


def with_deleted(*, state: State, ref: str) -> State:
    """
    Delete the pin with a ref.

    Parameters
    ----------
    state : State
        State.
    ref : str
        A pin ref.

    Returns
    -------
    State
        State without the pin; the same pin stays shown. When the pin
        shown is deleted, it becomes the unpinned place. Unchanged when
        no pin has the ref.
    """
    places = state.places
    index = ref_index(places=places, ref=ref)
    if index is None:
        return state
    if index == places.current and places.unpinned is None:
        return replace(state, places=with_unpinned(places=places))
    pins = (*places.pins[:index], *places.pins[index + 1:])
    current = places.current - (index < places.current)
    current = min(current, max(len(pins) - 1, 0))
    return replace(state, places=replace(places, pins=pins, current=current))


def with_moved(*, state: State, ref: str, steps: int) -> State:
    """
    Move the pin with a ref, stopping at either end.

    Parameters
    ----------
    state : State
        State.
    ref : str
        A pin ref.
    steps : int
        How far to move it; negative is earlier.

    Returns
    -------
    State
        State with the pin moved. Unchanged when no pin has the ref or
        it is already at the end.
    """
    places = state.places
    index = ref_index(places=places, ref=ref)
    if index is None:
        return state
    target = min(max(index + steps, 0), len(places.pins) - 1)
    if target == index:
        return state
    order = list(range(len(places.pins)))
    order.insert(target, order.pop(index))
    return replace(state, places=reordered(places=places, order=order))


def with_ordered(*, state: State, refs: Sequence[str]) -> State:
    """
    Put the pins in the order of their refs.

    Parameters
    ----------
    state : State
        State.
    refs : Sequence[str]
        The ref of every pin, in the new order.

    Returns
    -------
    State
        State with the pins in order. Unchanged unless `refs` names
        every pin exactly once.
    """
    places = state.places
    order = [ref_index(places=places, ref=ref) for ref in refs]
    found = [index for index in order if index is not None]
    if sorted(found) != list(range(len(places.pins))) or len(order) != len(
        found
    ):
        return state
    return replace(state, places=reordered(places=places, order=found))


def on_pin_edit(*, state: State, event: PinEdit) -> State:
    """
    Apply an edit to the pins.

    Parameters
    ----------
    state : State
        State.
    event : PinEdit
        A pin saved, deleted or moved, or the pins ordered.

    Returns
    -------
    State
        State with the edit applied. The same object when the edit is
        refused or changes nothing.
    """
    match event:
        case PinSaved(ref=ref, pin=pin):
            return with_saved(state=state, ref=ref, pin=pin)
        case PinDeleted(ref=ref):
            return with_deleted(state=state, ref=ref)
        case PinMoved(ref=ref, steps=steps):
            return with_moved(state=state, ref=ref, steps=steps)
        case PinsOrdered(refs=refs):
            return with_ordered(state=state, refs=refs)
