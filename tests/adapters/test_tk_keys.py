"""Tests for the monitor's key mapping (no window opened)."""

import pytest

from paperroute.adapters.tk_monitor import HoldKey, key_event
from paperroute.core.messages import (
    Event,
    Knob,
    KnobHeld,
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


def test_hold_key_long_is_hold_once() -> None:
    """Holding past 0.8 s gives one hold and no press on release."""
    key = HoldKey()
    key.down(0)
    assert key.poll(0.5) is None
    assert key.poll(0.8) == KnobHeld(knob=Knob.BOTTOM)
    assert key.poll(1.5) is None
    key.up(2)
    assert key.poll(2.1) is None


def test_hold_key_ignores_autorepeat() -> None:
    """Repeated downs while held do not restart the timer."""
    key = HoldKey()
    key.down(0)
    assert key.down(0.4) is None
    assert key.poll(0.8) == KnobHeld(knob=Knob.BOTTOM)


def test_hold_key_x11_up_down_pair_is_a_repeat() -> None:
    """An up then a down within the gap is autorepeat, not a release."""
    key = HoldKey()
    key.down(0)
    key.up(0.5)
    assert key.down(0.52) is None
    assert key.poll(0.9) == KnobHeld(knob=Knob.BOTTOM)
    assert key.poll(1.0) is None
