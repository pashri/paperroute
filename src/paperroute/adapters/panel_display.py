"""Draw frames on the e-paper panel from one worker thread."""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from paperroute.adapters.epaper import PanelTimeout
from paperroute.ports import Display, PanelRefresh
from paperroute.render.panel_bits import (
    HEIGHT,
    WIDTH,
    Box,
    changed_box,
    grey_planes,
    mono_patch,
    oriented,
    quick_planes,
)

CLEAN_AFTER: Final[int] = 30  # quick patches
CLEAN_QUIET: Final[float] = 30  # seconds
RETRY_AFTER: Final[float] = 60  # seconds
STOP_WAIT: Final[float] = 8  # seconds
SCREEN: Final[Box] = Box(left=0, top=0, right=WIDTH, bottom=HEIGHT)

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

    def draw_clear(self, *, bits: bytes) -> None:
        """
        Draw the whole screen in black and white, with no wipe.

        Parameters
        ----------
        bits : bytes
            The whole screen's 1-bit pixels, 1 for black.
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
    asleep : bool
        Whether the device is in Sleep mode.
    clear : bool
        Whether a clear refresh was asked for.
    """

    frame: NDArray[np.uint8]
    full: bool
    turned: bool
    quick: bool
    asleep: bool = False
    clear: bool = False


@dataclass(frozen=True)
class Clean:
    """Redraw the last frame with a full refresh."""


CLEAN: Final[Clean] = Clean()


@dataclass(frozen=True)
class Retry:
    """
    A failed frame to draw again.

    Attributes
    ----------
    job : Job
        The frame, as a full refresh.
    due : float
        When to draw it, in clock seconds.
    """

    job: Job
    due: float


class PanelDisplay:
    """
    Draws frames on an e-paper panel from one worker thread.

    Only the newest frame waits to be drawn. A full refresh draws four
    greys. A clear refresh draws the whole screen in black and white
    with no wipe. Every other change is a 1-bit quick patch that drives
    only the pixels whose value changed, so grey that did not change
    stays grey. After `CLEAN_AFTER` quick patches, the last frame gets
    a cleaning full refresh once no quick frame has come for
    `CLEAN_QUIET` seconds, unless the device is in Sleep mode. A frame
    shown in Sleep mode puts the panel into deep sleep once drawn. A
    failed refresh is tried again as a full refresh every `RETRY_AFTER`
    seconds until it works or a newer frame comes.

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
        self._asleep = False
        self._retry: Retry | None = None
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
        asleep: bool = False,
        clear: bool = False,
    ) -> None:
        """
        Pass on finished refreshes, then leave a frame for the worker.

        Never waits for the panel. A frame still waiting is replaced,
        keeping its request for a full or clear refresh.

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
        asleep : bool
            Whether the device is in Sleep mode: the panel deep-sleeps
            once the frame is drawn, and is not cleaned.
        clear : bool
            Whether to do a clear refresh, unless a full one is due.
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
            waiting = self._waiting
            self._waiting = Job(
                frame=kept,
                full=full or (waiting is not None and waiting.full),
                turned=turned,
                quick=quick,
                asleep=asleep,
                clear=clear or (waiting is not None and waiting.clear),
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
        Wait for a frame, a due retry or a due cleaning refresh.

        Returns
        -------
        Job | Clean | None
            The work to do, or None once stopped.
        """
        with self._ready:
            while not self._stopping:
                if (work := self._take()) is not None:
                    self._busy = True
                    return work
                self._busy = False
                self._ready.notify_all()
                self._ready.wait(timeout=self._wait())
            return None

    def _take(self) -> Job | Clean | None:
        """
        Take the work due now, newest frame first.

        Returns
        -------
        Job | Clean | None
            The waiting frame, the failed frame once `RETRY_AFTER` has
            passed, a due cleaning refresh, or None. The failed frame
            stays until a full refresh works.
        """
        if self._waiting is not None:
            job, self._waiting = self._waiting, None
            return job
        if self._retry is not None and self._clock() >= self._retry.due:
            return self._retry.job
        if self._clean_due():
            return CLEAN
        return None

    def _wait(self) -> float | None:
        """
        Give how long to wait before a retry or a clean falls due.

        Returns
        -------
        float | None
            Seconds, or None when neither is coming.
        """
        if self._retry is not None:
            return self._retry.due - self._clock()
        return self._clean_wait()

    def _clean_coming(self) -> bool:
        """
        Tell whether a cleaning refresh is owed once the knobs rest.

        Returns
        -------
        bool
            True when a frame is drawn, no full refresh is owed, the
            last frame was not shown in Sleep mode and `CLEAN_AFTER`
            quick patches have been drawn since the last full or clear
            refresh.
        """
        return (
            self._drawn is not None
            and not self._owed_full
            and not self._asleep
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
        whole = full or self._clears(work=work)
        started = self._clock()
        failing = self._retry is not None
        try:
            drew = self._draw(work=work, full=full)
            self._rest(work=work)
        except (PanelTimeout, OSError) as error:
            if not failing:
                logger.error('panel refresh failed', exc_info=error)
            self._failed(work=work, full=whole, started=started)
        except Exception:  # pylint: disable=broad-exception-caught
            if not failing:
                logger.exception('panel refresh failed')
            self._failed(work=work, full=whole, started=started)
        else:
            if drew:
                self._finish(full=whole, started=started, ok=True)

    @staticmethod
    def _clears(*, work: Job | Clean) -> bool:
        """
        Tell whether a clear refresh was asked for.

        Parameters
        ----------
        work : Job | Clean
            A frame or a cleaning refresh.

        Returns
        -------
        bool
            True for a frame asked to be drawn with a clear refresh.
        """
        return isinstance(work, Job) and work.clear

    def _rest(self, *, work: Job | Clean) -> None:
        """
        Deep-sleep the panel after a frame shown in Sleep mode.

        Parameters
        ----------
        work : Job | Clean
            The work just done.
        """
        if isinstance(work, Clean):
            return
        self._asleep = work.asleep
        if work.asleep:
            self._controller.sleep()

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
        if work.clear:
            self._clear(frame=work.frame)
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
        self._retry = None
        self._patches = 0
        self._drawn = frame

    def _clear(self, *, frame: NDArray[np.uint8]) -> None:
        """
        Draw a frame with a clear refresh, which clears the patch count.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame, already turned.
        """
        self._controller.draw_clear(bits=mono_patch(frame=frame, box=SCREEN))
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

    def _failed(
        self, *, work: Job | Clean, full: bool, started: float
    ) -> None:
        """
        Owe a full refresh, keep the frame to retry and report.

        Parameters
        ----------
        work : Job | Clean
            The work that failed.
        full : bool
            Whether the failed refresh was full.
        started : float
            When it started.
        """
        self._owed_full = True
        self._retry = Retry(
            job=self._redo(work=work),
            due=self._clock() + RETRY_AFTER,
        )
        self._finish(full=full, started=started, ok=False)

    def _redo(self, *, work: Job | Clean) -> Job:
        """
        Make the full refresh that tries failed work again.

        Parameters
        ----------
        work : Job | Clean
            The work that failed.

        Returns
        -------
        Job
            The failed frame, or the last frame for a failed clean.
        """
        if isinstance(work, Job):
            return replace(work, full=True, quick=False, clear=False)
        assert self._drawn is not None
        return Job(
            frame=self._drawn,
            full=True,
            turned=self._turned,
            quick=False,
            asleep=self._asleep,
        )

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
        asleep: bool = False,
        clear: bool = False,
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
        asleep : bool
            Whether the device is in Sleep mode.
        clear : bool
            Whether to do a clear refresh.
        """
        for display in self._displays:
            display.show(
                frame=frame,
                full=full,
                turned=turned,
                quick=quick,
                asleep=asleep,
                clear=clear,
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
