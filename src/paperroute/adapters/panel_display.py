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
    oriented,
    quick_planes,
)

CLEAN_AFTER: Final[int] = 30  # quick patches
CLEAN_QUIET: Final[float] = 30  # seconds
STOP_WAIT: Final[float] = 8  # seconds

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

    def draw_partial(
        self, *, box: Box, old: bytes, patch: bytes
    ) -> None:
        """
        Draw a 1-bit patch without wiping the rest of the panel.

        A pixel is driven only where `old` and `patch` differ.

        Parameters
        ----------
        box : Box
            The area to draw.
        old : bytes
            Rows of old bits, 1 for black.
        patch : bytes
            Rows of bits to draw, 1 for black.
        """

    def sleep(self) -> None:
        """Put the panel into deep sleep until the next draw."""

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
class Clean:
    """Redraw the last frame with a full refresh."""


CLEAN: Final[Clean] = Clean()


class PanelDisplay:
    """
    Draws frames on an e-paper panel from one worker thread.

    Only the newest frame waits to be drawn. A full refresh draws four
    greys. Every other change is a 1-bit quick patch that drives only
    the pixels whose value changed, so grey that did not change stays
    grey. After `CLEAN_AFTER` quick patches, the last frame gets a
    cleaning full refresh once no quick frame has come for
    `CLEAN_QUIET` seconds.

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
        self._patches = 0
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
            Whether the knob was just turned, which holds back cleaning.
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
        """Draw frames and cleaning refreshes until stopped."""
        while (work := self._next()) is not None:
            self._handle(work=work)

    def _next(self) -> Job | Clean | None:
        """
        Wait for a frame or a due cleaning refresh.

        Returns
        -------
        Job | Clean | None
            The work to do, or None once stopped.
        """
        with self._ready:
            while not self._stopping:
                if self._waiting is not None:
                    job, self._waiting = self._waiting, None
                    self._busy = True
                    return job
                if self._clean_due():
                    self._busy = True
                    return CLEAN
                self._busy = False
                self._ready.notify_all()
                self._ready.wait(timeout=self._clean_wait())
            return None

    def _clean_coming(self) -> bool:
        """
        Tell whether a cleaning refresh is owed once the knobs rest.

        Returns
        -------
        bool
            True when a frame is drawn, no full refresh is owed and
            `CLEAN_AFTER` quick patches have been drawn since the last
            full refresh.
        """
        return (
            self._drawn is not None
            and not self._owed_full
            and self._patches >= CLEAN_AFTER
        )

    def _clean_due(self) -> bool:
        """
        Tell whether to do a cleaning refresh now.

        Returns
        -------
        bool
            True when one is coming and no quick frame came for
            `CLEAN_QUIET` seconds.
        """
        return (
            self._clean_coming()
            and self._clock() - self._last_quick >= CLEAN_QUIET
        )

    def _clean_wait(self) -> float | None:
        """
        Give how long to wait before a cleaning refresh falls due.

        Returns
        -------
        float | None
            Seconds, or None when no cleaning refresh is coming.
        """
        if not self._clean_coming():
            return None
        return self._last_quick + CLEAN_QUIET - self._clock()

    def _handle(self, *, work: Job | Clean) -> None:
        """
        Do one piece of work, catching and reporting any failure.

        Parameters
        ----------
        work : Job | Clean
            A frame or a cleaning refresh.
        """
        full = isinstance(work, Clean) or self._needs_full(job=work)
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

    def _draw(self, *, work: Job | Clean, full: bool) -> bool:
        """
        Draw a frame or a cleaning refresh.

        Parameters
        ----------
        work : Job | Clean
            What to draw.
        full : bool
            Whether a frame gets a full refresh.

        Returns
        -------
        bool
            Whether anything was drawn.
        """
        if isinstance(work, Clean):
            assert self._drawn is not None
            self._full(frame=self._drawn)
            return True
        if full:
            self._full(frame=work.frame)
            self._turned = work.turned
            return True
        return self._patch(job=work)

    def _full(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Draw a frame with a full refresh, which clears the patch count.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame, already turned.
        """
        self._controller.draw_full(planes=grey_planes(frame=frame))
        self._owed_full = False
        self._patches = 0
        self._drawn = frame

    def _patch(self, *, job: Job) -> bool:
        """
        Draw the pixels a frame changed as one quick patch.

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
        old, patch = quick_planes(old=self._drawn, new=job.frame, box=box)
        self._controller.draw_partial(box=box, old=old, patch=patch)
        self._patches += 1
        self._drawn = job.frame
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
        quick: bool = False,
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
        quick : bool
            Whether a knob was turned just now.
        """
        for display in self._displays:
            display.show(
                frame=frame,
                full=full,
                turned=turned,
                quick=quick,
            )

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
