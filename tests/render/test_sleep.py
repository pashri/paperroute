"""Tests for the sleep band."""

import numpy as np

from paperroute.render.sleep import BAND_H, with_sleep_band
from paperroute_device.render.canvas import HEIGHT, INK, PAPER, WIDTH


def test_sleep_band_inverted() -> None:
    """The bottom rows are mostly ink and the rest is untouched."""
    frame = np.full((HEIGHT, WIDTH), PAPER, dtype=np.uint8)
    banded = with_sleep_band(frame=frame, band='Asleep - touch to wake')
    assert (banded[-BAND_H:] == INK).mean() > 0.8
    assert (banded[-BAND_H:] == PAPER).any()
    assert (banded[:-BAND_H] == PAPER).all()
    assert (frame == PAPER).all()
