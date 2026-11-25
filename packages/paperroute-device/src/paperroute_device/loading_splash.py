"""Show a city's loading picture while the board switches to it."""

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


def loading_image(*, key: str, folder: Path) -> Path | None:
    """
    Find the loading picture for an app.

    Parameters
    ----------
    key : str
        The app's key, ``paperroute-<city>``.
    folder : Path
        The folder holding the screens.

    Returns
    -------
    Path | None
        ``loading-<city>.raw`` in the folder, or None for a key that is
        not ``paperroute-<city>`` or a picture that is not there.
    """
    city = key.removeprefix(APP_PREFIX)
    if city == key or not city:
        return None
    image = folder / f'loading-{city}.raw'
    return image if image.is_file() else None


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
    folder : Path
        The folder holding the screens.
    turned : Callable[[], bool]
        Whether the panel is mounted upside down, read at each picture.
    """

    def __init__(
        self,
        *,
        display: Display,
        folder: Path,
        turned: Callable[[], bool],
    ) -> None:
        self._display = display
        self._folder = folder
        self._turned = turned
        self._lock = threading.Lock()
        self._holding = False
        self._last: Held | None = None

    def show(  # pylint: disable=too-many-arguments
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

        Never waits for the panel.

        Parameters
        ----------
        key : str
            The app being switched to.

        Raises
        ------
        ValueError
            When the picture is not exactly one frame.
        """
        image = loading_image(key=key, folder=self._folder)
        if image is None:
            return
        frame = load_frame(path=image)
        with self._lock:
            self._holding = True
        self._display.show(frame=frame, full=True, turned=self._turned())

    def release(self) -> None:
        """Stop holding, redrawing the newest board frame in full."""
        with self._lock:
            held = self._last if self._holding else None
            self._holding = False
        if held is not None:
            self._display.show(
                frame=held.frame, full=True, turned=held.turned,
            )
