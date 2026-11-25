"""Show a city's loading picture while the board switches to it."""

import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.ports import CLEAN_AFTER, Display
from paperroute_hardware.screen import load_frame

APP_PREFIX: Final[str] = 'paperroute-'

RELEASE_SCREENS: Final[Path] = (
    Path(sys.prefix).parent / 'deploy' / 'screens'
)  # the release's own screens, beside its .venv

logger = logging.getLogger(__name__)


def loading_name(*, key: str) -> str | None:
    """
    Name the loading picture for an app.

    Parameters
    ----------
    key : str
        The app's key, ``paperroute-<city>``.

    Returns
    -------
    str | None
        ``loading-<city>.raw``, or None for a key that is not
        ``paperroute-<city>``.
    """
    city = key.removeprefix(APP_PREFIX)
    if city == key or not city:
        return None
    return f'loading-{city}.raw'


def loading_image(*, key: str, folders: tuple[Path, ...]) -> Path | None:
    """
    Find the loading picture for an app.

    Parameters
    ----------
    key : str
        The app's key, ``paperroute-<city>``.
    folders : tuple[Path, ...]
        The folders holding the screens, searched in order.

    Returns
    -------
    Path | None
        ``loading-<city>.raw`` in the first folder that has it, or None
        for a key that is not ``paperroute-<city>`` or a picture that no
        folder has.
    """
    name = loading_name(key=key)
    if name is None:
        return None
    images = (folder / name for folder in folders)
    return next((image for image in images if image.is_file()), None)


@dataclass(frozen=True)
class Held:
    """
    A board frame kept back while the loading picture shows.

    Attributes
    ----------
    frame : NDArray[np.uint8]
        The frame.
    turned : bool
        Whether it is upside down.
    """

    frame: NDArray[np.uint8]
    turned: bool


class LoadingHold:
    """
    Passes board frames on, except while a loading picture shows.

    Once a loading picture is drawn, board frames are kept back until
    `release`, which draws the newest one in full.

    Parameters
    ----------
    display : Display
        The panel.
    folders : tuple[Path, ...]
        The folders holding the screens, searched in order.
    turned : Callable[[], bool]
        Whether the panel is mounted upside down, read at each picture.
    """

    def __init__(
        self,
        *,
        display: Display,
        folders: tuple[Path, ...],
        turned: Callable[[], bool],
    ) -> None:
        self._display = display
        self._folders = folders
        self._warned: set[str] = set()
        self._turned = turned
        self._lock = threading.Lock()
        self._holding = False
        self._last: Held | None = None

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
        asleep: bool = False,
        clear: bool = False,
        clean_after: int = CLEAN_AFTER,
    ) -> None:
        """
        Show a board frame, or keep it back behind the loading picture.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels, drawn the right way up.
        full : bool
            Whether to do a full refresh.
        turned : bool
            Whether to show the frame upside down.
        quick : bool
            Whether a knob was turned just now.
        asleep : bool
            Whether the device is in Sleep mode.
        clear : bool
            Whether to do a clear refresh.
        clean_after : int
            Quick patches before a cleaning full refresh.
        """
        with self._lock:
            self._last = Held(frame=frame, turned=turned)
            if self._holding:
                return
        self._display.show(
            frame=frame,
            turned=turned,
            full=full,
            clear=clear,
            quick=quick,
            asleep=asleep,
            clean_after=clean_after,
        )

    def settle(self, *, timeout: float) -> bool:
        """
        Wait for the panel, as `Display.settle` does.

        Parameters
        ----------
        timeout : float
            The longest wait, in seconds.

        Returns
        -------
        bool
            True when the panel has nothing left to draw.
        """
        return self._display.settle(timeout=timeout)

    def load(self, *, key: str) -> None:
        """
        Draw the loading picture for an app, if it has one.

        Never waits for the panel. Logs one warning per missing picture.

        Parameters
        ----------
        key : str
            The app being switched to.

        Raises
        ------
        ValueError
            When the picture is not exactly one frame.
        """
        image = loading_image(key=key, folders=self._folders)
        if image is None:
            self._warn_missing(key=key)
            return
        frame = load_frame(path=image)
        with self._lock:
            self._holding = True
        self._display.show(frame=frame, full=True, turned=self._turned())

    def _warn_missing(self, *, key: str) -> None:
        """
        Warn, once per picture, that an app's loading picture is missing.

        Parameters
        ----------
        key : str
            The app being switched to.
        """
        name = loading_name(key=key)
        if name is None or name in self._warned:
            return
        self._warned.add(name)
        logger.warning(
            'loading picture %s not found in %s',
            name,
            ', '.join(str(folder) for folder in self._folders),
        )

    def release(self) -> None:
        """Stop holding, redrawing the newest board frame in full."""
        with self._lock:
            held = self._last if self._holding else None
            self._holding = False
        if held is not None:
            self._display.show(
                frame=held.frame, full=True, turned=held.turned,
            )
