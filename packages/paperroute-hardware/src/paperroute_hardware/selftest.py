"""Draw a test card and a run of refreshes on the e-paper panel."""

import errno
import sys
import time
from collections.abc import Callable
from functools import partial
from typing import Final, TextIO

import numpy as np
from numpy.typing import NDArray

from paperroute_hardware.adapters.epaper import Uc8179
from paperroute_hardware.adapters.panel_bus import LinuxPanelBus
from paperroute_hardware.adapters.panel_display import PanelController
from paperroute_hardware.ports import PanelBus
from paperroute_hardware.render.panel_bits import (
    HEIGHT,
    WIDTH,
    changed_box,
    grey_planes,
    quick_planes,
)

__all__ = ['card', 'run_selftest', 'selftest_main']

BANDS: Final[tuple[int, ...]] = (255, 170, 85, 0)
BAR_LEFT: Final[int] = 40
BAR_RIGHT: Final[int] = 760
BAR_TOP: Final[int] = 400
BAR_BOTTOM: Final[int] = 440
OUTLINE: Final[int] = 4  # pixels
STEPS: Final[int] = 10
STEP_WIDTH: Final[int] = (BAR_RIGHT - BAR_LEFT) // STEPS  # pixels
FULL_LIMIT: Final[float] = 8  # seconds
PARTIAL_LIMIT: Final[float] = 1.0  # seconds
BUSY_MESSAGE: Final[str] = (
    'The panel is in use. Stop paperroute first: '
    'sudo systemctl stop paperroute'
)
BUSY_EXIT: Final[int] = 2
NO_PANEL_MESSAGE: Final[str] = 'No panel on this machine'


def card() -> NDArray[np.uint8]:
    """
    Draw the test card: four grey bands and an empty counter bar.

    Returns
    -------
    NDArray[np.uint8]
        The frame, white to black from left to right, with a white bar
        inside a black outline.
    """
    frame = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    band = WIDTH // len(BANDS)
    for index, grey in enumerate(BANDS):
        frame[:, index * band:(index + 1) * band] = grey
    frame[
        BAR_TOP - OUTLINE:BAR_BOTTOM + OUTLINE,
        BAR_LEFT - OUTLINE:BAR_RIGHT + OUTLINE,
    ] = 0
    frame[BAR_TOP:BAR_BOTTOM, BAR_LEFT:BAR_RIGHT] = 255
    return frame


def timed(*, clock: Callable[[], float], draw: Callable[[], None]) -> float:
    """
    Time one call.

    Parameters
    ----------
    clock : Callable[[], float]
        Gives the time in seconds.
    draw : Callable[[], None]
        What to time.

    Returns
    -------
    float
        Seconds the call took.
    """
    started = clock()
    draw()
    return clock() - started


def draw_steps(
    *,
    controller: PanelController,
    clock: Callable[[], float],
    out: TextIO,
) -> list[float]:
    """
    Draw the card, then the ten partial refreshes filling the bar.

    Parameters
    ----------
    controller : PanelController
        The panel.
    clock : Callable[[], float]
        Gives the time in seconds.
    out : TextIO
        Where the times are printed.

    Returns
    -------
    list[float]
        The full refresh time followed by each partial's time.
    """
    frame = card()
    planes = grey_planes(frame=frame)
    times = [timed(clock=clock, draw=lambda: controller.draw_full(
        planes=planes,
    ))]
    print(f'full: {times[0]:.1f} s', file=out)
    for step in range(1, STEPS + 1):
        new = frame.copy()
        new[
            BAR_TOP:BAR_BOTTOM,
            BAR_LEFT:BAR_LEFT + STEP_WIDTH * step,
        ] = 0
        box = changed_box(old=frame, new=new)
        assert box is not None
        old, patch = quick_planes(old=frame, new=new, box=box)
        elapsed = timed(
            clock=clock,
            draw=partial(
                controller.draw_partial, box=box, old=old, patch=patch,
            ),
        )
        times.append(elapsed)
        print(f'partial {step}: {elapsed:.2f} s', file=out)
        frame = new
    return times


def verdict(*, times: list[float]) -> str | None:
    """
    Find the first reason the self-test failed.

    Parameters
    ----------
    times : list[float]
        The full refresh time followed by each partial's time.

    Returns
    -------
    str | None
        The reason, or None when every time is within its limit.
    """
    if times[0] > FULL_LIMIT:
        return f'full refresh {times[0]:.2f} s'
    slowest = max(times[1:])
    if slowest > PARTIAL_LIMIT:
        return f'slowest partial {slowest:.2f} s'
    return None


def run_selftest(
    *,
    controller: PanelController,
    out: TextIO,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """
    Run the self-test, print the times and the verdict, close the panel.

    Parameters
    ----------
    controller : PanelController
        The panel.
    out : TextIO
        Where the report is printed.
    clock : Callable[[], float]
        Gives the time in seconds.

    Returns
    -------
    int
        0 for a pass, 1 for a fail.
    """
    try:
        times = draw_steps(controller=controller, clock=clock, out=out)
        reason = verdict(times=times)
    except Exception as error:  # pylint: disable=broad-exception-caught
        reason = f'{type(error).__name__}: {error}'
    finally:
        controller.close()
    print('PASS' if reason is None else f'FAIL: {reason}', file=out)
    return 0 if reason is None else 1


def selftest_main(
    *, opener: Callable[[], PanelBus] = LinuxPanelBus.open
) -> int:
    """
    Open the panel and run the self-test on it.

    Parameters
    ----------
    opener : Callable[[], PanelBus]
        Opens the panel's bus.

    Returns
    -------
    int
        The exit status: 0 pass, 1 fail or no panel here, 2 when the
        panel is in use.

    Raises
    ------
    OSError
        When the bus cannot be opened for a reason other than being busy.
    """
    try:
        bus = opener()
    except ImportError:
        print(NO_PANEL_MESSAGE, file=sys.stderr)
        return 1
    except OSError as error:
        if error.errno != errno.EBUSY:
            raise
        print(BUSY_MESSAGE, file=sys.stderr)
        return BUSY_EXIT
    return run_selftest(controller=Uc8179(bus=bus), out=sys.stdout)
