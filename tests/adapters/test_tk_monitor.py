"""Tests for the monitor's frame turning (no window opened)."""

import numpy as np

from paperroute.adapters.tk_monitor import turned_frame
from paperroute_device.render.canvas import HEIGHT, WIDTH


def test_turned_frame_rotates() -> None:
    """A turned frame is upside down; an unturned one is unchanged."""
    frame = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    frame[0, 0] = 0
    turned = turned_frame(frame=frame, turned=True)
    assert turned[HEIGHT - 1, WIDTH - 1] == 0
    assert turned[0, 0] == 255
    assert np.array_equal(turned_frame(frame=frame, turned=False), frame)
