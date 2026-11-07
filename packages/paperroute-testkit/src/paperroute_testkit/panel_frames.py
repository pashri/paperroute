"""A test frame for the panel, and the planes a fake bus was sent."""

from typing import cast

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.render.panel_bits import HEIGHT, WIDTH
from paperroute_testkit.hardware_fakes import FakePanelBus


def stepped_frame() -> NDArray[np.uint8]:
    """
    Make a frame whose top-left corner differs from the rest.

    Returns
    -------
    NDArray[np.uint8]
        White, with a black block at the top left.
    """
    frame = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    frame[:40, :80] = 0
    return frame


def drawn_planes(*, bus: FakePanelBus) -> tuple[bytes, bytes]:
    """
    Get the last grey planes a bus was sent.

    Parameters
    ----------
    bus : FakePanelBus
        The bus.

    Returns
    -------
    tuple[bytes, bytes]
        The data of the last 0x10 and 0x13 commands.
    """
    sent = [
        cast(tuple[int, bytes], entry)
        for kind, entry in bus.log
        if kind == 'command'
    ]
    first = [data for code, data in sent if code == 0x10]
    second = [data for code, data in sent if code == 0x13]
    return first[-1], second[-1]
