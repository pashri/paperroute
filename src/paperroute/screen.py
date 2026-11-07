"""Show one picture on the e-paper panel and leave it there."""

import argparse
import json
import sys
from contextlib import suppress
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.epaper import PanelTimeout, Uc8179
from paperroute.adapters.panel_bus import LinuxPanelBus
from paperroute.render.panel_bits import HEIGHT, WIDTH, grey_planes, oriented

__all__ = ['load_frame', 'read_rotate', 'screen_main', 'show_frame']


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


def read_rotate(*, path: Path | None) -> bool:
    """
    Read the rotate setting from the app's state file.

    Parameters
    ----------
    path : Path | None
        The state file, or None when there is none.

    Returns
    -------
    bool
        True only when the file holds ``settings.rotate`` as true; a
        missing, unreadable or malformed file counts as not rotated.
    """
    if path is None:
        return False
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    settings = data.get('settings') if isinstance(data, dict) else None
    return isinstance(settings, dict) and settings.get('rotate') is True


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


def screen_main(*, argv: list[str]) -> int:
    """
    Show a picture on the panel.

    Parameters
    ----------
    argv : list[str]
        The command-line arguments: ``--image`` names the picture, and
        ``--state`` optionally names the app's state file, whose rotate
        setting turns the picture upside down.

    Returns
    -------
    int
        0 when shown, 1 when the picture is bad or the panel is not
        usable (missing, busy or not answering).
    """
    parser = argparse.ArgumentParser(prog='paperroute.screen')
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--state', type=Path, default=None)
    args = parser.parse_args(argv)
    turned = read_rotate(path=args.state)
    try:
        frame = load_frame(path=args.image)
        show_frame(
            panel=Uc8179(bus=LinuxPanelBus.open()),
            frame=frame,
            turned=turned,
        )
    except (ValueError, OSError, PanelTimeout, ImportError) as error:
        print(f'screen: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(screen_main(argv=sys.argv[1:]))
