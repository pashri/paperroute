"""Tests for turning knob switch and turn edges into knob events."""

import pytest

from paperroute_hardware.core.knob_decoder import (
    Encoder,
    KnobDecoder,
    swapped_event,
)
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobHeldTurned,
    KnobInput,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_hardware.core.knob_hold import HOLD_S


def _decoder(*, swap: bool = False) -> KnobDecoder:
    """A decoder whose swap setting is fixed."""
    return KnobDecoder(swapped=lambda: swap)


@pytest.mark.parametrize(('encoder', 'knob'), [
    (Encoder.A, Knob.TOP),
    (Encoder.B, Knob.BOTTOM),
])
@pytest.mark.parametrize(('clockwise', 'steps'), [(True, 1), (False, -1)])
def test_turned_right_and_left(
    encoder: Encoder, knob: Knob, clockwise: bool, steps: int,
) -> None:
    """A detent is one step: clockwise is right, anticlockwise left."""
    events = _decoder().turned(encoder=encoder, clockwise=clockwise, now=0)
    assert events == [KnobTurned(knob=knob, steps=steps)]


def test_pressed_then_released_quickly_is_a_press() -> None:
    """A release within the hold time is a press, sent on release."""
    decoder = _decoder()
    assert decoder.pressed(encoder=Encoder.B, now=0) == []
    assert decoder.poll(now=0.1) == []
    assert decoder.released(encoder=Encoder.B, now=0.2) == [
        KnobPressed(knob=Knob.BOTTOM),
    ]
    assert decoder.poll(now=0.3) == []


@pytest.mark.parametrize(('held', 'expected'), [
    (HOLD_S - 0.01, KnobPressed(knob=Knob.BOTTOM)),
    (HOLD_S, KnobHeld(knob=Knob.BOTTOM)),
    (HOLD_S + 1, KnobHeld(knob=Knob.BOTTOM)),
])
def test_released_at_the_hold_boundary(
    held: float, expected: KnobInput,
) -> None:
    """A release at 0.8 s or later is a hold; sooner is a press."""
    decoder = _decoder()
    decoder.pressed(encoder=Encoder.B, now=0)
    assert decoder.poll(now=held) == []
    assert decoder.released(encoder=Encoder.B, now=held) == [expected]


def test_turn_while_held_is_a_held_turn_with_nothing_after() -> None:
    """Turning the held knob gives held turns, and no press or hold."""
    decoder = _decoder()
    decoder.pressed(encoder=Encoder.B, now=0)
    assert decoder.turned(encoder=Encoder.B, clockwise=True, now=0.3) == [
        KnobHeldTurned(knob=Knob.BOTTOM, steps=1),
    ]
    assert decoder.turned(encoder=Encoder.B, clockwise=False, now=0.4) == [
        KnobHeldTurned(knob=Knob.BOTTOM, steps=-1),
    ]
    assert decoder.released(encoder=Encoder.B, now=2.0) == []
    assert decoder.poll(now=3.0) == []


def test_other_knob_turns_plainly_while_one_is_held() -> None:
    """Holding the bottom knob leaves the top knob's turns as they are."""
    decoder = _decoder()
    decoder.pressed(encoder=Encoder.B, now=0)
    assert decoder.turned(encoder=Encoder.A, clockwise=True, now=0.3) == [
        KnobTurned(knob=Knob.TOP, steps=1),
    ]
    assert decoder.released(encoder=Encoder.B, now=0.5) == [
        KnobPressed(knob=Knob.BOTTOM),
    ]


def test_both_held_is_knobs_held_once() -> None:
    """Both held for the hold time is one KnobsHeld, and nothing else."""
    decoder = _decoder()
    decoder.pressed(encoder=Encoder.A, now=0)
    decoder.pressed(encoder=Encoder.B, now=0.1)
    assert decoder.poll(now=0.1 + HOLD_S - 0.01) == []
    assert decoder.poll(now=0.1 + HOLD_S + 0.01) == [KnobsHeld()]
    assert decoder.poll(now=2.0) == []
    assert decoder.released(encoder=Encoder.A, now=2.1) == []
    assert decoder.released(encoder=Encoder.B, now=2.2) == []
    assert decoder.poll(now=3.0) == []


def test_both_let_go_early_sends_nothing() -> None:
    """Letting go of both before the hold time sends nothing."""
    decoder = _decoder()
    decoder.pressed(encoder=Encoder.A, now=0)
    decoder.pressed(encoder=Encoder.B, now=0.1)
    assert decoder.released(encoder=Encoder.B, now=0.3) == []
    assert decoder.released(encoder=Encoder.A, now=0.4) == []
    assert decoder.poll(now=2.0) == []


def test_release_without_press_sends_nothing() -> None:
    """A release seen with no press before it is ignored."""
    assert _decoder().released(encoder=Encoder.A, now=1.0) == []


@pytest.mark.parametrize(('encoder', 'knob'), [
    (Encoder.A, Knob.BOTTOM),
    (Encoder.B, Knob.TOP),
])
def test_swap_puts_a_at_the_bottom_and_b_at_the_top(
    encoder: Encoder, knob: Knob,
) -> None:
    """With the swap on, knob A plays the bottom and B the top."""
    decoder = _decoder(swap=True)
    assert decoder.turned(encoder=encoder, clockwise=True, now=0) == [
        KnobTurned(knob=knob, steps=1),
    ]
    decoder.pressed(encoder=encoder, now=1.0)
    assert decoder.turned(encoder=encoder, clockwise=False, now=1.1) == [
        KnobHeldTurned(knob=knob, steps=-1),
    ]
    decoder.released(encoder=encoder, now=1.2)
    decoder.pressed(encoder=encoder, now=2.0)
    assert decoder.released(encoder=encoder, now=2.1) == [
        KnobPressed(knob=knob),
    ]
    decoder.pressed(encoder=encoder, now=3.0)
    assert decoder.released(encoder=encoder, now=3.0 + HOLD_S + 0.1) == [
        KnobHeld(knob=knob),
    ]


def test_swap_is_read_at_each_event() -> None:
    """A change to the setting applies to the next event."""
    swap = [False]
    decoder = KnobDecoder(swapped=lambda: swap[0])
    assert decoder.turned(encoder=Encoder.A, clockwise=True, now=0) == [
        KnobTurned(knob=Knob.TOP, steps=1),
    ]
    swap[0] = True
    assert decoder.turned(encoder=Encoder.A, clockwise=True, now=1) == [
        KnobTurned(knob=Knob.BOTTOM, steps=1),
    ]


@pytest.mark.parametrize(('event', 'expected'), [
    (KnobTurned(knob=Knob.TOP, steps=2), KnobTurned(knob=Knob.BOTTOM, steps=2)),
    (KnobPressed(knob=Knob.BOTTOM), KnobPressed(knob=Knob.TOP)),
    (KnobHeld(knob=Knob.TOP), KnobHeld(knob=Knob.BOTTOM)),
    (
        KnobHeldTurned(knob=Knob.BOTTOM, steps=-1),
        KnobHeldTurned(knob=Knob.TOP, steps=-1),
    ),
    (KnobsHeld(), KnobsHeld()),
])
def test_swapped_event(event: KnobInput, expected: KnobInput) -> None:
    """The swap trades top and bottom and leaves KnobsHeld alone."""
    assert swapped_event(event=event, swap=True) == expected
    assert swapped_event(event=event, swap=False) == event
