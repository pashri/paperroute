"""Frame and text helpers the device's screens share."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import Canvas, Face, text_width

HEADER: Final[int] = 40


def finish(canvas: Canvas) -> NDArray[np.uint8]:
    """
    Threshold a canvas to a 1-bit frame.

    Parameters
    ----------
    canvas : Canvas
        Drawn canvas.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    grey = canvas.grey()
    return np.where(grey < 128, 0, 255).astype(np.uint8)


def wrap(*, text: str, face: Face, width: float) -> tuple[str, ...]:
    """
    Break text into lines at spaces, filling each line greedily.

    Parameters
    ----------
    text : str
        Text.
    face : Face
        Font.
    width : float
        Available width in pixels.

    Returns
    -------
    tuple[str, ...]
        Lines; a single word wider than the width gets a line of its own.
    """
    lines: list[str] = []
    for word in text.split():
        joined = f'{lines[-1]} {word}' if lines else word
        if lines and text_width(text=joined, face=face) <= width:
            lines[-1] = joined
        else:
            lines.append(word)
    return tuple(lines)
