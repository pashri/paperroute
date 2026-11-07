"""Show one picture on the e-paper panel and leave it there."""

import sys
from contextlib import suppress
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.adapters.epaper import PanelTimeout, Uc8179
from paperroute_hardware.adapters.panel_bus import LinuxPanelBus
from paperroute_hardware.render.panel_bits import (
    HEIGHT,
    WIDTH,
    grey_planes,
    oriented,
)

__all__ = ['load_frame', 'show_frame', 'show_image']


def load_frame(*, path: Path) -> NDArray[np.uint8]:
    """
    Read a raw 8-bit grey picture, row by row.

    Parameters
    ----------
    path : Path
        The file.

    Returns
    -------
    NDArray[np.uint8]
        The frame, `HEIGHT` rows of `WIDTH` pixels.

    Raises
    ------
    ValueError
        When the file is not exactly one frame.
    """
    data = path.read_bytes()
    if len(data) != WIDTH * HEIGHT:
        raise ValueError(
            f'{path} has {len(data)} bytes, not {WIDTH * HEIGHT}'
        )
    return np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH)


def show_frame(
    *,
    panel: Uc8179,
    frame: NDArray[np.uint8],
    turned: bool = False,
) -> None:
    """
    Draw a frame with a full refresh, then power the panel off.

    The panel is powered off even when the draw fails.

    Parameters
    ----------
    panel : Uc8179
        The panel's controller.
    frame : NDArray[np.uint8]
        The frame to show, right way up.
    turned : bool
        Whether to show it upside down, as the app does when rotated.

    Raises
    ------
    PanelTimeout
        When the panel stays busy too long.
    OSError
        When the panel stops answering on the bus.
    """
    try:
        panel.draw_full(
            planes=grey_planes(frame=oriented(frame=frame, turned=turned)),
        )
    except (OSError, PanelTimeout):
        with suppress(OSError, PanelTimeout):
            panel.close()
        raise
    panel.close()


def show_image(*, image: Path, turned: bool) -> int:
    """
    Show a raw picture file on the panel.

    Parameters
    ----------
    image : Path
        The picture, one raw 8-bit grey frame.
    turned : bool
        Whether to show it upside down.

    Returns
    -------
    int
        0 when shown, 1 when the picture is bad or the panel is not
        usable (missing, busy or not answering); the error goes to
        stderr.
    """
    try:
        frame = load_frame(path=image)
        show_frame(
            panel=Uc8179(bus=LinuxPanelBus.open()),
            frame=frame,
            turned=turned,
        )
    except (ValueError, OSError, PanelTimeout, ImportError) as error:
        print(f'screen: {error}', file=sys.stderr)
        return 1
    return 0
