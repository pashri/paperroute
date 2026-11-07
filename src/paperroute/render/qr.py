"""Draw QR codes as 1-bit images."""

from typing import Final

import numpy as np
import segno
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER

QUIET: Final[int] = 4  # modules of paper round the code


def qr_image(*, text: str, module_px: int) -> NDArray[np.uint8]:
    """
    Encode text as a QR code at error level M, quiet zone included.

    Parameters
    ----------
    text : str
        What the code holds.
    module_px : int
        Width of one module in pixels.

    Returns
    -------
    NDArray[np.uint8]
        A square image, 0 for ink and 255 for paper.
    """
    code = segno.make_qr(text, error='m', boost_error=False)
    dark = np.array(code.matrix, dtype=bool)
    padded = np.pad(dark, QUIET)
    scaled = padded.repeat(module_px, axis=0).repeat(module_px, axis=1)
    return np.where(scaled, INK, PAPER).astype(np.uint8)
