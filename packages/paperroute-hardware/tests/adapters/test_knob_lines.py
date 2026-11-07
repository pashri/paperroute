"""Tests for the knobs' wiring."""

from paperroute_hardware.adapters.knob_lines import DT_LINES, LINES, Role
from paperroute_hardware.core.knob_decoder import Encoder


def test_lines_match_the_wiring() -> None:
    """Knob A is on GPIO 20, 21 and 26; knob B on 5, 6 and 16."""
    assert LINES == {
        20: (Encoder.A, Role.CLK),
        21: (Encoder.A, Role.DT),
        26: (Encoder.A, Role.SW),
        5: (Encoder.B, Role.CLK),
        6: (Encoder.B, Role.DT),
        16: (Encoder.B, Role.SW),
    }
    assert DT_LINES == {Encoder.A: 21, Encoder.B: 6}
