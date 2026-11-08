"""Tests for the monitor's key mapping (no window opened)."""

import pytest

from paperroute.adapters.tk_monitor import key_event
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_hardware.core.knob_hold import HOLD_S, Chord, HoldKey
from paperroute_transit.core.messages import Event, QuitRequested


@pytest.mark.parametrize(('keysym', 'expected'), [
    ('a', KnobTurned(knob=Knob.TOP, steps=-1)),
    ('d', KnobTurned(knob=Knob.TOP, steps=1)),
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
    key = HoldKey(knob=Knob.BOTTOM)
    assert key.down(0) is None
    key.up(0.2)
    assert key.poll(0.21) is None
    assert key.poll(0.3) == KnobPressed(knob=Knob.BOTTOM)
    assert key.poll(0.4) is None


def test_hold_key_long_is_hold_on_release() -> None:
    """Holding past 0.8 s gives one hold, once the key is let go."""
    key = HoldKey(knob=Knob.BOTTOM)
    key.down(0)
    assert key.poll(0.5) is None
    assert key.poll(1.5) is None
    key.up(2)
    assert key.poll(2.01) is None
    assert key.poll(2.1) == KnobHeld(knob=Knob.BOTTOM)
    assert key.poll(2.2) is None


def test_hold_key_ignores_autorepeat() -> None:
    """Repeated downs while held do not restart the timer."""
    key = HoldKey(knob=Knob.BOTTOM)
    key.down(0)
    assert key.down(0.4) is None
    key.up(0.9)
    assert key.poll(1.0) == KnobHeld(knob=Knob.BOTTOM)


def test_hold_key_x11_up_down_pair_is_a_repeat() -> None:
    """An up then a down within the gap is autorepeat, not a release."""
    key = HoldKey(knob=Knob.BOTTOM)
    key.down(0)
    key.up(0.5)
    assert key.down(0.52) is None
    assert key.poll(0.9) is None
    key.up(1.0)
    assert key.poll(1.1) == KnobHeld(knob=Knob.BOTTOM)


def test_hold_key_turn_while_held_selects_not_pins() -> None:
    """Hold K and press L: a turn while held, and no hold on release."""
    key = HoldKey(knob=Knob.BOTTOM)
    key.down(0)
    turn = key.steered(key_event(keysym='l'))
    assert turn == KnobHeldTurned(knob=Knob.BOTTOM, steps=1)
    key.up(2)
    assert key.poll(2.1) is None
    assert key.poll(3) is None


def test_hold_key_short_turn_while_held_is_no_press() -> None:
    """A quick K with J inside it is a turn while held, not a press."""
    key = HoldKey(knob=Knob.BOTTOM)
    key.down(0)
    assert key.steered(key_event(keysym='j')) == KnobHeldTurned(
        knob=Knob.BOTTOM, steps=-1,
    )
    key.up(0.2)
    assert key.poll(0.3) is None


def test_hold_key_steers_only_the_bottom_turn_while_down() -> None:
    """Other keys, and L with K up, pass through unchanged."""
    key = HoldKey(knob=Knob.BOTTOM)
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
    key = HoldKey(knob=Knob.BOTTOM)
    key.down(0)
    key.cancel()
    assert key.poll(1.0) is None
    key.up(1.1)
    assert key.poll(1.5) is None


def test_key_event_leaves_s_to_the_hold_key() -> None:
    """S is resolved by HoldKey, not the plain mapping."""
    assert key_event(keysym='s') is None


def _keys() -> tuple[HoldKey, HoldKey, Chord]:
    """The top and bottom hold keys and the chord over them."""
    top = HoldKey(knob=Knob.TOP)
    bottom = HoldKey(knob=Knob.BOTTOM)
    return top, bottom, Chord(top=top, bottom=bottom)


def _poll(
    *, keys: tuple[HoldKey, HoldKey, Chord], now: float
) -> list[Event]:
    """What the monitor posts on a pump: the chord, then each key."""
    top, bottom, chord = keys
    events = [chord.poll(now=now), top.poll(now), bottom.poll(now)]
    return [event for event in events if event is not None]


def _down(
    *, keys: tuple[HoldKey, HoldKey, Chord], key: HoldKey, now: float
) -> None:
    """Press a key and tell the chord, as the monitor does."""
    key.down(now)
    keys[2].down(now=now)


def test_s_tap_is_top_press() -> None:
    """A quick tap of S is a top press."""
    keys = _keys()
    _down(keys=keys, key=keys[0], now=0.0)
    keys[0].up(0.1)
    assert _poll(keys=keys, now=0.2) == [KnobPressed(knob=Knob.TOP)]


def test_both_keys_held_open_settings() -> None:
    """S and K together for the hold time send one KnobsHeld."""
    keys = _keys()
    _down(keys=keys, key=keys[0], now=0.0)
    _down(keys=keys, key=keys[1], now=0.1)
    assert _poll(keys=keys, now=0.1 + HOLD_S - 0.05) == []
    assert _poll(keys=keys, now=0.1 + HOLD_S + 0.05) == [KnobsHeld()]
    assert _poll(keys=keys, now=2.0) == []
    keys[0].up(2.2)
    keys[1].up(2.2)
    assert _poll(keys=keys, now=2.3) == []
    assert _poll(keys=keys, now=2.4) == []


def test_held_k_joined_by_s_is_settings_only() -> None:
    """K held, then S: Settings, with no hold or press for either."""
    keys = _keys()
    _down(keys=keys, key=keys[1], now=0.0)
    _down(keys=keys, key=keys[0], now=0.9)
    assert _poll(keys=keys, now=0.9 + HOLD_S + 0.05) == [KnobsHeld()]
    keys[0].up(2.0)
    keys[1].up(2.0)
    assert _poll(keys=keys, now=2.1) == []
    assert _poll(keys=keys, now=2.2) == []


def test_chord_released_early_sends_nothing() -> None:
    """Letting go before the hold time sends nothing at all."""
    keys = _keys()
    _down(keys=keys, key=keys[0], now=0.0)
    _down(keys=keys, key=keys[1], now=0.1)
    keys[1].up(0.5)
    keys[0].up(0.6)
    for now in (0.7, 0.8, 1.0, 1.5, 2.0):
        assert _poll(keys=keys, now=now) == []


def test_chord_ignores_autorepeat() -> None:
    """X11 up and down pairs on both keys keep the chord going."""
    keys = _keys()
    _down(keys=keys, key=keys[0], now=0.0)
    _down(keys=keys, key=keys[1], now=0.1)
    for now in (0.3, 0.5):
        keys[0].up(now)
        keys[1].up(now)
        _down(keys=keys, key=keys[0], now=now + 0.01)
        _down(keys=keys, key=keys[1], now=now + 0.01)
        assert _poll(keys=keys, now=now + 0.02) == []
    assert _poll(keys=keys, now=0.1 + HOLD_S + 0.05) == [KnobsHeld()]


def test_top_hold_alone_is_top_hold() -> None:
    """S held alone sends KnobHeld(TOP), which does nothing."""
    keys = _keys()
    _down(keys=keys, key=keys[0], now=0.0)
    keys[0].up(HOLD_S + 0.2)
    assert _poll(keys=keys, now=HOLD_S + 0.3) == [
        KnobHeld(knob=Knob.TOP)
    ]


def test_top_key_steers_only_top_turns() -> None:
    """Hold S and press D: a top turn while held; J passes through."""
    top = HoldKey(knob=Knob.TOP)
    top.down(0)
    assert top.steered(key_event(keysym='d')) == KnobHeldTurned(
        knob=Knob.TOP, steps=1,
    )
    bottom_turn = key_event(keysym='j')
    assert top.steered(bottom_turn) == bottom_turn
