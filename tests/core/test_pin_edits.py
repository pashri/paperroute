"""Tests for the pin-edit events."""

from dataclasses import replace
from datetime import time, timedelta

from paperroute_device.core.kit_effects import SaveState
from paperroute_device.core.kit_events import Tick
from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.kit_steps import kit_of, kit_step, step
from paperroute_testkit.core.steps import written
from paperroute_transit.core.messages import (
    Effect,
    LoadDepartures,
    PinDeleted,
    PinMoved,
    PinSaved,
    PinsOrdered,
)
from paperroute_transit.core.model import AutoWindow, Pin, pin_ref
from paperroute_transit.core.pin_edits import on_pin_edit
from paperroute_transit.core.places import toggled
from paperroute_transit.core.state_model import Places, State
from tests.core.test_places import FIRST as A
from tests.core.test_places import SECOND as B
from tests.core.test_places import THIRD as C
from tests.core.test_places import state_with
from tests.core.test_sleep import asleep
from tests.core.test_state import effects_of

D = Pin(origin=replace(A.origin, stop_id='fourth'))


def ref(pin: Pin) -> str:
    """
    Name a pin by its ref.

    Parameters
    ----------
    pin : Pin
        Pin.

    Returns
    -------
    str
        Its ref.
    """
    return pin_ref(pin=pin)


def three() -> State:
    """
    Make a state with pins A, B and C and B shown.

    Returns
    -------
    State
        The state.
    """
    return state_with(current=1)


def test_add_goes_last_unselected() -> None:
    """A new pin goes at the end and the shown pin stays."""
    state, _ = step(three(), PinSaved(ref=None, pin=D))
    assert state.places.pins == (A, B, C, D)
    assert state.places.current == 1


def test_add_existing_place_is_ignored() -> None:
    """Adding a place already pinned changes nothing."""
    state = three()
    pin = replace(A, nickname='x')
    assert on_pin_edit(state=state, event=PinSaved(ref=None, pin=pin)) is state


def test_save_replaces_in_place() -> None:
    """Saving a pin over itself keeps order and current, and loads nothing."""
    event = PinSaved(ref=ref(C), pin=replace(C, nickname='Gym'))
    state, effects = step(three(), event)
    assert state.places.pins == (A, B, replace(C, nickname='Gym'))
    assert state.places.current == 1
    assert not effects_of(effects=effects, kind=LoadDepartures)


def test_save_onto_other_pin_is_ignored() -> None:
    """A save that would make two pins the same place is refused."""
    state = three()
    event = PinSaved(ref=ref(C), pin=A)
    assert on_pin_edit(state=state, event=event) is state


def test_save_sorts_and_dedupes_routes_and_cleans_nickname() -> None:
    """The route filter is sorted and unique; the nickname is tidied."""
    pin = replace(C, only=('T9', 'T2', 'T9'), nickname='  Big   gym  ')
    state, _ = step(three(), PinSaved(ref=ref(C), pin=pin))
    saved = state.places.pins[2]
    assert saved.only == ('T2', 'T9')
    assert saved.nickname == 'Big gym'


def test_saving_shown_pin_reloads_board() -> None:
    """A new identity for the shown pin empties the board and reloads."""
    state = three()
    state = replace(
        state,
        board=replace(
            state.board,
            departures=(departure(departs=START + timedelta(minutes=3)),),
            loaded_until=START + timedelta(hours=1),
        ),
    )
    new = replace(B, only=('T2',))
    state, effects = step(state, PinSaved(ref=ref(B), pin=new))
    assert not state.board.departures
    assert state.board.loaded_until is None
    loads = effects_of(effects=effects, kind=LoadDepartures)
    assert [load.pin for load in loads] == [new]


def test_delete_other_pin_keeps_current() -> None:
    """Deleting an earlier pin keeps the same pin shown."""
    state, _ = step(three(), PinDeleted(ref=ref(A)))
    assert state.places.pins == (B, C)
    assert state.places.current == 0


def test_delete_shown_pin_unpins_it() -> None:
    """Deleting the shown pin leaves it on screen, unpinned."""
    state, effects = step(three(), PinDeleted(ref=ref(B)))
    assert state.places.pins == (A, C)
    assert state.places.unpinned == B
    assert not effects_of(effects=effects, kind=LoadDepartures)


WINDOW = AutoWindow(days=frozenset({0}), start=time(7), end=time(9))


def test_delete_drops_windows() -> None:
    """Deleting a pin deletes its windows too."""
    timed = replace(B, windows=(WINDOW,))
    other = replace(A, windows=(WINDOW,))
    state = state_with(pins=(other, timed, C), current=1)
    state, _ = step(state, PinDeleted(ref=ref(timed)))
    assert state.places.unpinned is not None
    assert state.places.unpinned.windows == ()
    state, _ = step(state, PinDeleted(ref=ref(other)))
    assert state.places.pins == (C,)


def test_unpin_keeps_windows() -> None:
    """Unpinning a pin keeps its windows on the unpinned place."""
    timed = replace(B, windows=(WINDOW,))
    state = toggled(state=state_with(pins=(A, timed, C), current=1))
    assert state.places.unpinned == timed


def test_move_up_and_clamp() -> None:
    """A pin moves one step; moving past the end changes nothing."""
    state, _ = step(three(), PinMoved(ref=ref(C), steps=-1))
    assert state.places.pins == (A, C, B)
    assert state.places.current == 2
    first = three()
    event = PinMoved(ref=ref(A), steps=-1)
    assert on_pin_edit(state=first, event=event) is first


def test_order_applies_and_current_follows() -> None:
    """A drag result reorders the pins and keeps the shown pin."""
    event = PinsOrdered(refs=(ref(C), ref(A), ref(B)))
    state, _ = step(three(), event)
    assert state.places.pins == (C, A, B)
    assert state.places.current == 2


def test_move_with_stale_ref_is_ignored() -> None:
    """A ref that is no longer pinned changes nothing."""
    state = three()
    event = PinMoved(ref=ref(D), steps=1)
    assert on_pin_edit(state=state, event=event) is state
    assert on_pin_edit(state=state, event=PinDeleted(ref=ref(D))) is state


def test_order_with_other_pins_is_ignored() -> None:
    """Refs that are not exactly the current pins change nothing."""
    state = three()
    for refs in ((ref(A), ref(B)), (ref(A), ref(B), ref(C), ref(D))):
        event = PinsOrdered(refs=refs)
        assert on_pin_edit(state=state, event=event) is state


def saves(*, effects: tuple[Effect, ...]) -> list[SaveState]:
    """
    Pick the saves from some effects.

    Parameters
    ----------
    effects : tuple[Effect, ...]
        Effects.

    Returns
    -------
    list[SaveState]
        The saves.
    """
    return effects_of(effects=effects, kind=SaveState)


def test_edit_is_saved() -> None:
    """A Tick past the save delay saves the edited pins."""
    kit, _ = kit_step(
        kit=kit_of(state=three()), event=PinMoved(ref=ref(C), steps=-1),
    )
    late = Tick(now=kit.device.now + timedelta(seconds=4))
    _, effects = kit_step(kit=kit, event=late)
    found = saves(effects=effects)
    assert found
    assert written(save=found[0]).pins == (A, C, B)


def test_edit_while_asleep_stays_asleep() -> None:
    """An edit while asleep reorders and saves but does not wake."""
    state = replace(asleep(), places=Places(pins=(A, B, C)))
    event = PinMoved(ref=ref(A), steps=1)
    kit, _ = kit_step(kit=kit_of(state=state), event=event)
    assert kit.app.places.pins == (B, A, C)
    assert kit.device.panel.asleep
    late = Tick(now=kit.device.now + timedelta(seconds=4))
    kit, effects = kit_step(kit=kit, event=late)
    assert saves(effects=effects)
    assert kit.device.panel.asleep
