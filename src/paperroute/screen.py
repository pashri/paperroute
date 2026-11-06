"""Show one picture on the e-paper panel and leave it there."""

import argparse
import sys
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.epaper import PanelTimeout, Uc8179
from paperroute.adapters.panel_bus import LinuxPanelBus
from paperroute.render.panel_bits import HEIGHT, WIDTH, grey_planes

__all__ = ['load_frame', 'screen_main', 'show_frame']


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


def show_frame(*, panel: Uc8179, frame: NDArray[np.uint8]) -> None:
    """
    Draw a frame with a full refresh, then power the panel off.

    Parameters
    ----------
    panel : Uc8179
        The panel's controller.
    frame : NDArray[np.uint8]
        The frame to show.
    """
    panel.draw_full(planes=grey_planes(frame=frame))
    panel.close()


def screen_main(*, argv: list[str]) -> int:
    """
    Show a picture on the panel.

    Parameters
    ----------
    argv : list[str]
        The command-line arguments.

    Returns
    -------
    int
        0 when shown, 1 when the picture is bad or the panel is not
        usable (missing, busy or not answering).
    """
    parser = argparse.ArgumentParser(prog='paperroute.screen')
    parser.add_argument('--image', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        frame = load_frame(path=args.image)
        show_frame(panel=Uc8179(bus=LinuxPanelBus.open()), frame=frame)
    except (ValueError, OSError, PanelTimeout, ImportError) as error:
        print(f'screen: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(screen_main(argv=sys.argv[1:]))
