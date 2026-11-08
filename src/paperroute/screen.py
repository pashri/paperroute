"""Show one picture on the e-paper panel and leave it there."""

import argparse
import json
import sys
from pathlib import Path

from paperroute_hardware.screen import show_image

__all__ = ['read_rotate', 'screen_main']


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
        True only when the file holds ``device.settings.rotate`` as true,
        or, in a file the app has not yet migrated, ``settings.rotate``;
        a missing, unreadable or malformed file counts as not rotated.
    """
    if path is None:
        return False
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    device = data.get('device')
    holder = device if isinstance(device, dict) else data
    settings = holder.get('settings')
    return isinstance(settings, dict) and settings.get('rotate') is True


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
    return show_image(image=args.image, turned=read_rotate(path=args.state))


if __name__ == '__main__':
    sys.exit(screen_main(argv=sys.argv[1:]))
