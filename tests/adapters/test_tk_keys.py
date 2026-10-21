"""Tests for the monitor's key mapping (no window opened)."""

import pytest

from paperroute.adapters.tk_monitor import HoldKey, key_event
from paperroute.core.messages import (
    Event,
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobTurned,
    QuitRequested,
)


@pytest.mark.parametrize(('keysym', 'expected'), [
    ('a', KnobTurned(knob=Knob.TOP, steps=-1)),
    ('d', KnobTurned(knob=Knob.TOP, steps=1)),
    ('s', KnobPressed(knob=Knob.TOP)),
    ('j', KnobTurned(knob=Knob.BOTTOM, steps=-1)),
    ('l', KnobTurned(knob=Knob.BOTTOM, steps=1)),
    ('q', QuitRequested()),
    ('Escape', QuitRequested()),
])
def test_key_event_mapping(keysym: str, expected: Event) -> None:
    """Left-hand keys play the top knob, right-hand keys the bottom."""
    assert key_event(keysym=keysym) == expected


def test_key_event_ignores_other_keys() -> None:
    """Unmapped keys do nothing."""
    assert key_event(keysym='x') is None


def test_key_event_leaves_k_to_the_hold_key() -> None:
    """K is resolved by HoldKey, not the plain mapping."""
    assert key_event(keysym='k') is None


def test_hold_key_short_is_press() -> None:
    """A quick tap gives a press once the repeat gap has passed."""
    key = HoldKey()
    assert key.down(0) is None
    key.up(0.2)
    assert key.poll(0.21) is None
    assert key.poll(0.3) == KnobPressed(knob=Knob.BOTTOM)
    assert key.poll(0.4) is None


def test_hold_key_long_is_hold_on_release() -> None:
    """Holding past 0.8 s gives one hold, once the key is let go."""
    key = HoldKey()
    key.down(0)
    assert key.poll(0.5) is None
    assert key.poll(1.5) is None
    key.up(2)
    assert key.poll(2.01) is None
    assert key.poll(2.1) == KnobHeld(knob=Knob.BOTTOM)
    assert key.poll(2.2) is None


def test_hold_key_ignores_autorepeat() -> None:
    """Repeated downs while held do not restart the timer."""
    key = HoldKey()
    key.down(0)
    assert key.down(0.4) is None
    key.up(0.9)
    assert key.poll(1.0) == KnobHeld(knob=Knob.BOTTOM)


def test_hold_key_x11_up_down_pair_is_a_repeat() -> None:
    """An up then a down within the gap is autorepeat, not a release."""
    key = HoldKey()
    key.down(0)
    key.up(0.5)
    assert key.down(0.52) is None
    assert key.poll(0.9) is None
    key.up(1.0)
    assert key.poll(1.1) == KnobHeld(knob=Knob.BOTTOM)


def test_hold_key_turn_while_held_selects_not_pins() -> None:
    """Hold K and press L: a turn while held, and no hold on release."""
    key = HoldKey()
    key.down(0)
    turn = key.steered(key_event(keysym='l'))
    assert turn == KnobHeldTurned(knob=Knob.BOTTOM, steps=1)
    key.up(2)
    assert key.poll(2.1) is None
    assert key.poll(3) is None


def test_hold_key_short_turn_while_held_is_no_press() -> None:
    """A quick K with J inside it is a turn while held, not a press."""
    key = HoldKey()
    key.down(0)
    assert key.steered(key_event(keysym='j')) == KnobHeldTurned(
        knob=Knob.BOTTOM, steps=-1,
    )
    key.up(0.2)
    assert key.poll(0.3) is None


def test_hold_key_steers_only_the_bottom_turn_while_down() -> None:
    """Other keys, and L with K up, pass through unchanged."""
    key = HoldKey()
    right = key_event(keysym='l')
    assert key.steered(right) == right
    key.down(0)
    top = key_event(keysym='d')
    assert key.steered(top) == top
    assert key.steered(None) is None
    key.up(0.1)
    assert key.poll(0.2) == KnobPressed(knob=Knob.BOTTOM)


def test_hold_key_cancel_sends_nothing() -> None:
    """Cancelling drops a pending hold and the later release."""
    key = HoldKey()
    key.down(0)
    key.cancel()
    assert key.poll(1.0) is None
    key.up(1.1)
    assert key.poll(1.5) is None
