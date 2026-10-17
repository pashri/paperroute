"""Tests for the monitor's key mapping (no window opened)."""

import pytest

from paperroute.adapters.tk_monitor import key_event
from paperroute.core.messages import (
    Event,
    Knob,
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
    ('k', KnobPressed(knob=Knob.BOTTOM)),
    ('q', QuitRequested()),
    ('Escape', QuitRequested()),
])
def test_key_event_mapping(keysym: str, expected: Event) -> None:
    """Left-hand keys play the top knob, right-hand keys the bottom."""
    assert key_event(keysym=keysym) == expected


def test_key_event_ignores_other_keys() -> None:
    """Unmapped keys do nothing."""
    assert key_event(keysym='x') is None
