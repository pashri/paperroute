"""Draw the sleep band over a frame, and the kit's plain sleep screen."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.core.panel_views import SLEEP_BAND, PlainSleepView
from paperroute_device.render.canvas import (
    HEIGHT,
    INK,
    PAPER,
    WIDTH,
    Canvas,
    Face,
)
from paperroute_device.render.frame import finish

BAND_H: Final[int] = SLEEP_BAND
BAND_FACE: Final[Face] = Face(weight=700, px=24)


def with_sleep_band(
    *,
    frame: NDArray[np.uint8],
    band: str,
) -> NDArray[np.uint8]:
    """
    Put an inverted band with centred text across a frame's bottom.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A 1-bit frame; it is not changed.
    band : str
        The band's text.

    Returns
    -------
    NDArray[np.uint8]
        A copy of the frame with the band.
    """
    canvas = Canvas()
    canvas.rect(box=(0, HEIGHT - BAND_H, WIDTH, HEIGHT), grey=INK)
    canvas.text(
        at=(WIDTH / 2, HEIGHT - BAND_H / 2),
        text=canvas.fit(text=band, face=BAND_FACE, width=WIDTH - 64),
        face=BAND_FACE,
        grey=PAPER,
        anchor='mm',
    )
    banded = frame.copy()
    banded[-BAND_H:] = finish(canvas)[-BAND_H:]
    return banded


def draw_plain_sleep(*, view: PlainSleepView) -> NDArray[np.uint8]:
    """
    Draw the kit's plain sleep screen.

    Parameters
    ----------
    view : PlainSleepView
        The screen.

    Returns
    -------
    NDArray[np.uint8]
        A blank paper frame under the sleep band.
    """
    return with_sleep_band(frame=finish(Canvas()), band=view.band)
