"""Show the ON picture once per boot."""

import logging
from pathlib import Path
from typing import Final

from paperroute_device.screen import read_rotate
from paperroute_hardware.adapters.panel_display import PanelDisplay

SPLASH_IMAGE: Final[Path] = Path(
    '/usr/local/share/paperroute/deploy/screens/on.raw',
)
SPLASH_MARKER: Final[Path] = Path('/dev/shm/paperroute-splash')

logger = logging.getLogger(__name__)


def splash_once(
    *,
    panel: PanelDisplay | None,
    image: Path,
    marker: Path,
    state: Path,
) -> bool:
    """
    Ask the panel to draw the picture, once per boot.

    The marker file records that this boot has had its splash.

    Parameters
    ----------
    panel : PanelDisplay | None
        The panel display; None when there is no panel.
    image : Path
        A raw 8-bit grey picture of the whole screen.
    marker : Path
        A file that exists once the splash has been asked for.
    state : Path
        The app's state file, read for the rotate setting.

    Returns
    -------
    bool
        True when the splash was asked for.
    """
    if panel is None:
        return False
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return False
    except OSError as error:
        logger.info('splash skipped: %s', error)
        return False
    panel.splash(image=image, turned=read_rotate(path=state))
    return True
