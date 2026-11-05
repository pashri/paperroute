"""Draw frames on the e-paper panel from one worker thread."""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.epaper import PanelTimeout
from paperroute.ports import Display, PanelRefresh
from paperroute.render.panel_bits import (
    Box,
    changed_box,
    grey_planes,
    has_grey,
    mono_patch,
    oriented,
    union,
)

TIDY_AFTER: Final[float] = 3  # seconds
STOP_WAIT: Final[float] = 10  # seconds

logger = logging.getLogger(__name__)


class PanelController(Protocol):
    """Runs refresh sequences on an e-paper controller."""

    def draw_full(self, *, planes: tuple[bytes, bytes]) -> None:
        """
        Wipe the panel, then draw four greys.

        Parameters
        ----------
        planes : tuple[bytes, bytes]
            The two grey planes for the whole screen.
        """

    def draw_partial(self, *, box: Box, patch: bytes) -> None:
        """
        Draw a 1-bit patch without wiping the rest of the panel.

        Parameters
        ----------
        box : Box
            The area to draw.
        patch : bytes
            Rows of bits, 1 for black.
        """

    def draw_grey_patch(
        self,
        *,
        box: Box,
        planes: tuple[bytes, bytes],
    ) -> None:
        """
        Draw a four-grey patch without wiping the rest of the panel.

        Parameters
        ----------
        box : Box
            The area to draw.
        planes : tuple[bytes, bytes]
            The two grey planes, cut to the box.
        """

    def close(self) -> None:
        """Put the panel to sleep and release it."""


@dataclass(frozen=True)
class Job:
    """
    A frame waiting to be drawn.

    Attributes
    ----------
    frame : NDArray[np.uint8]
        The frame as it goes to the panel, already turned.
    full : bool
        Whether a full refresh was asked for.
    turned : bool
        Whether the frame is turned.
    quick : bool
        Whether the knob was just turned.
    """

    frame: NDArray[np.uint8]
    full: bool
    turned: bool
    quick: bool


@dataclass(frozen=True)
class Tidy:
    """Redraw the dotted area in four greys."""


TIDY: Final[Tidy] = Tidy()


class PanelDisplay:
    """
    Draws frames on an e-paper panel from one worker thread.

    Only the newest frame waits to be drawn. Grey changes shown while
    the knob turns are dotted in 1 bit, then redrawn in grey once
    `TIDY_AFTER` seconds pass with no quick frame.

    Parameters
    ----------
    controller : PanelController
        The panel's controller; only the worker thread uses it.
    report : Callable[[PanelRefresh], None]
        Receives each refresh, on the thread that calls `show`.
    clock : Callable[[], float]
        Gives the time in seconds.
    """

    def __init__(
        self,
        *,
        controller: PanelController,
        report: Callable[[PanelRefresh], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._controller = controller
        self._report = report
        self._clock = clock
        self._ready = threading.Condition()
        self._waiting: Job | None = None
        self._results: list[PanelRefresh] = []
        self._last_quick = float('-inf')
        self._busy = False
        self._stopping = False
        self._drawn: NDArray[np.uint8] | None = None
        self._turned = False
        self._owed_full = True
        self._dotted: Box | None = None
        self._failing = False
        self._thread = threading.Thread(
            target=self._run,
            name='panel',
            daemon=True,
        )
        self._thread.start()

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
    ) -> None:
        """
        Pass on finished refreshes, then leave a frame for the worker.

        Never waits for the panel. A frame still waiting is replaced,
        keeping its request for a full refresh.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels, drawn the right way up. The panel
            keeps its own copy.
        full : bool
            Whether to do a full refresh.
        turned : bool
            Whether to show the frame upside down.
        quick : bool
            Whether the knob was just turned, so grey may be dotted.
        """
        with self._ready:
            results, self._results = self._results, []
        for refresh in results:
            self._report(refresh)
        kept = (
            np.ascontiguousarray(oriented(frame=frame, turned=True))
            if turned
            else frame.copy()
        )
        with self._ready:
            asked = self._waiting is not None and self._waiting.full
            self._waiting = Job(
                frame=kept, full=full or asked, turned=turned, quick=quick,
            )
            if quick:
                self._last_quick = self._clock()
            self._ready.notify_all()

    def settle(self, *, timeout: float) -> bool:
        """
        Wait until every frame shown so far is on the panel.

        The last refresh may have failed. Refreshes are not passed on.

        Parameters
        ----------
        timeout : float
            Seconds to wait at most.

        Returns
        -------
        bool
            True when nothing waits and the worker is idle.
        """
        with self._ready:
            return self._ready.wait_for(self._idle, timeout=timeout)

    def stop(self) -> None:
        """
        Stop the worker, dropping any frame still waiting.

        The controller is closed only if the worker ends within
        `STOP_WAIT` seconds; a worker stuck in a refresh is left behind.
        """
        with self._ready:
            self._stopping = True
            self._waiting = None
            self._ready.notify_all()
        self._thread.join(timeout=STOP_WAIT)
        if self._thread.is_alive():
            logger.warning('panel still busy; left it open')
            return
        self._controller.close()

    def _idle(self) -> bool:
        """
        Tell whether every frame shown is on the panel.

        Returns
        -------
        bool
            True when nothing waits and the worker is idle.
        """
        return self._waiting is None and not self._busy

    def _run(self) -> None:
        """Draw frames and tidy-ups until stopped."""
        while (work := self._next()) is not None:
            self._handle(work=work)

    def _next(self) -> Job | Tidy | None:
        """
        Wait for a frame or a due tidy-up.

        Returns
        -------
        Job | Tidy | None
            The work to do, or None once stopped.
        """
        with self._ready:
            while not self._stopping:
                if self._waiting is not None:
                    job, self._waiting = self._waiting, None
                    self._busy = True
                    return job
                if self._tidy_due():
                    self._busy = True
                    return TIDY
                self._busy = False
                self._ready.notify_all()
                self._ready.wait(timeout=self._tidy_wait())
            return None

    def _tidy_due(self) -> bool:
        """
        Tell whether the dotted area should be redrawn now.

        Returns
        -------
        bool
            True when there is a dotted area, no full refresh is owed
            and no quick frame came for `TIDY_AFTER` seconds.
        """
        return (
            self._dotted is not None
            and not self._owed_full
            and self._clock() - self._last_quick >= TIDY_AFTER
        )

    def _tidy_wait(self) -> float | None:
        """
        Give how long to wait before a tidy-up falls due.

        Returns
        -------
        float | None
            Seconds, or None when no tidy-up is coming.
        """
        if self._dotted is None or self._owed_full:
            return None
        return self._last_quick + TIDY_AFTER - self._clock()

    def _handle(self, *, work: Job | Tidy) -> None:
        """
        Do one piece of work, catching and reporting any failure.

        Parameters
        ----------
        work : Job | Tidy
            A frame or a tidy-up.
        """
        full = isinstance(work, Job) and self._needs_full(job=work)
        started = self._clock()
        try:
            drew = self._draw(work=work, full=full)
        except (PanelTimeout, OSError) as error:
            if not self._failing:
                logger.error('panel refresh failed', exc_info=error)
            self._failed(full=full, started=started)
        except Exception:  # pylint: disable=broad-exception-caught
            if not self._failing:
                logger.exception('panel refresh failed')
            self._failed(full=full, started=started)
        else:
            if drew:
                self._failing = False
                self._finish(full=full, started=started, ok=True)

    def _needs_full(self, *, job: Job) -> bool:
        """
        Tell whether a frame must be a full refresh.

        Parameters
        ----------
        job : Job
            The frame.

        Returns
        -------
        bool
            True when asked for, owed, or turned unlike the last frame.
        """
        return job.full or self._owed_full or job.turned != self._turned

    def _draw(self, *, work: Job | Tidy, full: bool) -> bool:
        """
        Draw a frame or a tidy-up.

        Parameters
        ----------
        work : Job | Tidy
            What to draw.
        full : bool
            Whether a frame gets a full refresh.

        Returns
        -------
        bool
            Whether anything was drawn.
        """
        if isinstance(work, Tidy):
            return self._tidy()
        if full:
            self._controller.draw_full(planes=grey_planes(frame=work.frame))
            self._turned = work.turned
            self._owed_full = False
            self._dotted = None
            self._drawn = work.frame
            return True
        return self._patch(job=work)

    def _patch(self, *, job: Job) -> bool:
        """
        Draw the box a frame changed, dotting grey when quick.

        Parameters
        ----------
        job : Job
            The frame.

        Returns
        -------
        bool
            Whether anything was drawn.
        """
        assert self._drawn is not None
        box = changed_box(old=self._drawn, new=job.frame)
        if box is None:
            return False
        grey = has_grey(frame=job.frame, box=box)
        if grey and not job.quick:
            area = union(a=self._dotted, b=box)
            self._controller.draw_grey_patch(
                box=area, planes=grey_planes(frame=job.frame, box=area),
            )
            self._dotted = None
        else:
            self._controller.draw_partial(
                box=box, patch=mono_patch(frame=job.frame, box=box),
            )
            if grey:
                self._dotted = union(a=self._dotted, b=box)
        self._drawn = job.frame
        return True

    def _tidy(self) -> bool:
        """
        Redraw the dotted area of the last frame in four greys.

        Returns
        -------
        bool
            Always True.
        """
        assert self._drawn is not None and self._dotted is not None
        self._controller.draw_grey_patch(
            box=self._dotted,
            planes=grey_planes(frame=self._drawn, box=self._dotted),
        )
        self._dotted = None
        return True

    def _failed(self, *, full: bool, started: float) -> None:
        """
        Owe a full refresh and report the failure.

        Parameters
        ----------
        full : bool
            Whether the failed refresh was full.
        started : float
            When it started.
        """
        self._failing = True
        self._owed_full = True
        self._finish(full=full, started=started, ok=False)

    def _finish(self, *, full: bool, started: float, ok: bool) -> None:
        """
        Queue a refresh for the next `show` to pass on.

        Parameters
        ----------
        full : bool
            Whether it was full.
        started : float
            When it started.
        ok : bool
            Whether it worked.
        """
        refresh = PanelRefresh(
            full=full, seconds=self._clock() - started, ok=ok,
        )
        with self._ready:
            self._results.append(refresh)


class Displays:
    """
    Shows each frame on several displays.

    Parameters
    ----------
    displays : Sequence[Display]
        The displays, in order.
    """

    def __init__(self, *, displays: Sequence[Display]) -> None:
        self._displays = list(displays)

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
    ) -> None:
        """
        Show a frame on every display.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            480 by 800 grey pixels, drawn the right way up.
        full : bool
            Whether to do a full refresh.
        turned : bool
            Whether to show the frame upside down.
        """
        for display in self._displays:
            display.show(frame=frame, full=full, turned=turned)

    def settle(self, *, timeout: float) -> bool:
        """
        Wait for every display to show every frame.

        Each display is given the whole timeout in turn.

        Parameters
        ----------
        timeout : float
            Seconds each display may wait at most.

        Returns
        -------
        bool
            True only when every display has.
        """
        settled = [
            display.settle(timeout=timeout) for display in self._displays
        ]
        return all(settled)
