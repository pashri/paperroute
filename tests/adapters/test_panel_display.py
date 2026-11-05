"""Tests for the e-paper panel display worker."""

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters import panel_display
from paperroute.adapters.epaper import PanelTimeout
from paperroute.adapters.panel_display import (
    CLEAN_AFTER,
    CLEAN_QUIET,
    Displays,
    PanelDisplay,
)
from paperroute.ports import PanelRefresh
from paperroute.render.panel_bits import (
    Box,
    grey_planes,
    mono_patch,
    quick_planes,
)
from tests.fakes import FrameRecorder

WAIT: Final[float] = 5  # seconds
BOX_A: Final[Box] = Box(left=16, top=10, right=32, bottom=20)
BOX_B: Final[Box] = Box(left=400, top=300, right=424, bottom=310)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        """
        Read the time.

        Returns
        -------
        float
            Seconds.
        """
        return self.now


class CreepingClock(Clock):
    """A clock that moves a little every time it is read."""

    def __call__(self) -> float:
        """
        Read the time, then move it on.

        Returns
        -------
        float
            Seconds.
        """
        self.now += 0.002
        return self.now


@dataclass
class GatedController:
    """
    A panel controller that records draws and waits on a gate in each.

    Attributes
    ----------
    calls : list[tuple[str, object]]
        ('full', planes), ('partial', (box, patch)) and ('sleep', None),
        in the order they were asked for.
    olds : list[bytes]
        The `old` bits of each partial, in order.
    gate : threading.Event
        Each draw waits for this; it starts set.
    entered : threading.Event
        Set when a draw starts.
    failures : list[Exception]
        Raised by the next draws, one each, in order.
    closed : bool
        Whether `close` was called.
    """

    calls: list[tuple[str, object]] = field(default_factory=list)
    olds: list[bytes] = field(default_factory=list)
    gate: threading.Event = field(default_factory=threading.Event)
    entered: threading.Event = field(default_factory=threading.Event)
    failures: list[Exception] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        """Open the gate."""
        self.gate.set()

    def draw(self, *, kind: str, args: object) -> None:
        """
        Record a draw once the gate opens, raising if a failure waits.

        Parameters
        ----------
        kind : str
            'full' or 'partial'.
        args : object
            What it was given.

        Raises
        ------
        Exception
            The next of `failures`, if any.
        """
        self.entered.set()
        assert self.gate.wait(timeout=WAIT)
        self.calls.append((kind, args))
        if self.failures:
            raise self.failures.pop(0)

    def draw_full(self, *, planes: tuple[bytes, bytes]) -> None:
        """
        Record a full refresh.

        Parameters
        ----------
        planes : tuple[bytes, bytes]
            The grey planes.
        """
        self.draw(kind='full', args=planes)

    def draw_partial(
        self, *, box: Box, old: bytes, patch: bytes
    ) -> None:
        """
        Record a 1-bit patch.

        Parameters
        ----------
        box : Box
            The area.
        old : bytes
            The bits the panel shows now.
        patch : bytes
            The bits.
        """
        self.olds.append(old)
        self.draw(kind='partial', args=(box, patch))

    def sleep(self) -> None:
        """Record a deep sleep."""
        self.calls.append(('sleep', None))

    def close(self) -> None:
        """Record the close."""
        self.closed = True

    def kinds(self) -> list[str]:
        """
        List the kinds of draw asked for.

        Returns
        -------
        list[str]
            One kind per draw.
        """
        return [kind for kind, _ in self.calls]


@dataclass
class Rig:
    """
    A panel display on a gated controller and a hand-moved clock.

    Attributes
    ----------
    display : PanelDisplay
        The display.
    controller : GatedController
        Its controller.
    clock : Clock
        Its clock.
    reports : list[PanelRefresh]
        Refreshes handed to `report`.
    """

    display: PanelDisplay
    controller: GatedController
    clock: Clock
    reports: list[PanelRefresh]

    def show(
        self,
        *,
        frame: NDArray[np.uint8],
        full: bool = False,
        turned: bool = False,
        quick: bool = False,
    ) -> None:
        """
        Show a frame and wait for the panel to settle.

        Parameters
        ----------
        frame : NDArray[np.uint8]
            The frame.
        full : bool
            Whether to ask for a full refresh.
        turned : bool
            Whether to turn it.
        quick : bool
            Whether the knob was just turned.
        """
        self.display.show(frame=frame, full=full, turned=turned, quick=quick)
        assert self.display.settle(timeout=WAIT)


@pytest.fixture(name='rig')
def _rig() -> Iterator[Rig]:
    """
    Run a panel display, stopping it afterwards.

    Yields
    ------
    Rig
        The display and its fakes.
    """
    controller = GatedController()
    clock = Clock()
    reports: list[PanelRefresh] = []
    display = PanelDisplay(
        controller=controller,
        report=reports.append,
        clock=clock,
    )
    rig = Rig(
        display=display,
        controller=controller,
        clock=clock,
        reports=reports,
    )
    yield rig
    controller.gate.set()
    rig.display.stop()


def white() -> NDArray[np.uint8]:
    """
    Make an all-white frame.

    Returns
    -------
    NDArray[np.uint8]
        The frame.
    """
    return np.full((480, 800), 255, dtype=np.uint8)


def painted(
    *,
    boxes: tuple[Box, ...],
    grey: int,
    frame: NDArray[np.uint8] | None = None,
) -> NDArray[np.uint8]:
    """
    Fill boxes of a frame with one grey.

    Parameters
    ----------
    boxes : tuple[Box, ...]
        The areas.
    grey : int
        The value.
    frame : NDArray[np.uint8] | None
        The frame to start from, or None for white.

    Returns
    -------
    NDArray[np.uint8]
        A new frame.
    """
    out = white() if frame is None else frame.copy()
    for box in boxes:
        out[box.top : box.bottom, box.left : box.right] = grey
    return out


def test_first_frame_is_full(rig: Rig) -> None:
    """The first frame is a full refresh of the whole screen."""
    rig.show(frame=white())
    assert rig.controller.calls == [('full', grey_planes(frame=white()))]


def test_unchanged_frame_draws_nothing(rig: Rig) -> None:
    """Showing the same frame again draws nothing."""
    rig.show(frame=white())
    rig.show(frame=white())
    assert rig.controller.kinds() == ['full']


def test_change_draws_partial_box(rig: Rig) -> None:
    """A black and white change is a 1-bit patch of its box."""
    frame = painted(boxes=(BOX_A,), grey=0)
    rig.show(frame=white())
    rig.show(frame=frame)
    assert rig.controller.calls[1:] == [
        ('partial', (BOX_A, mono_patch(frame=frame, box=BOX_A))),
    ]


def test_partial_sends_old_picture_of_last_frame(rig: Rig) -> None:
    """The old bits come from the last frame and the new one."""
    first = painted(boxes=(BOX_A,), grey=0)
    second = painted(boxes=(BOX_B,), grey=0, frame=first)
    rig.show(frame=first)
    rig.show(frame=second)
    old, _ = quick_planes(old=first, new=second, box=BOX_B)
    assert rig.controller.olds == [old]
    assert rig.controller.calls[1:] == [
        ('partial', (BOX_B, mono_patch(frame=second, box=BOX_B))),
    ]


def test_show_keeps_full_through_replacement(rig: Rig) -> None:
    """A waiting full refresh survives later frames replacing it."""
    a = painted(boxes=(BOX_A,), grey=0)
    b = painted(boxes=(BOX_B,), grey=0)
    c = painted(boxes=(BOX_A, BOX_B), grey=0)
    rig.controller.gate.clear()
    rig.display.show(frame=white())
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.show(frame=a, full=True)
    rig.display.show(frame=b)
    rig.display.show(frame=c)
    rig.controller.gate.set()
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.calls[1:] == [('full', grey_planes(frame=c))]


def test_turned_change_is_full_and_rotated(rig: Rig) -> None:
    """Turning the panel is a full refresh of the turned frame."""
    frame = painted(boxes=(BOX_A,), grey=0)
    later = painted(boxes=(BOX_B,), grey=0, frame=frame)
    turned = np.ascontiguousarray(later[::-1, ::-1])
    rig.show(frame=frame)
    rig.show(frame=frame, turned=True)
    rig.show(frame=later, turned=True)
    box = Box(left=376, top=170, right=400, bottom=180)
    assert rig.controller.calls[1:] == [
        ('full', grey_planes(frame=np.ascontiguousarray(frame[::-1, ::-1]))),
        ('partial', (box, mono_patch(frame=turned, box=box))),
    ]


def test_failed_refresh_forces_full_and_keeps_going(
    rig: Rig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failures log once per streak, report not ok and force a full."""
    frame = painted(boxes=(BOX_A,), grey=0)
    rig.show(frame=white())
    rig.controller.failures.extend((PanelTimeout('busy'), OSError('spi')))
    with caplog.at_level(logging.ERROR):
        rig.show(frame=frame)
        rig.show(frame=white())
        rig.show(frame=frame)
    rig.show(frame=frame)
    assert rig.controller.kinds() == ['full', 'partial', 'full', 'full']
    assert [record.message for record in caplog.records] == [
        'panel refresh failed',
    ]
    assert [(r.full, r.ok) for r in rig.reports] == [
        (True, True),
        (False, False),
        (True, False),
        (True, True),
    ]


def test_unexpected_error_keeps_the_worker_going(
    rig: Rig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any other error is logged with its trace and forces a full."""
    frame = painted(boxes=(BOX_A,), grey=0)
    rig.show(frame=white())
    rig.controller.failures.append(RuntimeError('bug'))
    with caplog.at_level(logging.ERROR):
        rig.show(frame=frame)
    rig.show(frame=white())
    assert rig.controller.kinds() == ['full', 'partial', 'full']
    [record] = caplog.records
    assert record.exc_info is not None


def test_settle_waits_and_times_out(rig: Rig) -> None:
    """Settle times out while the panel draws and succeeds after."""
    rig.controller.gate.clear()
    rig.display.show(frame=white())
    assert rig.controller.entered.wait(timeout=WAIT)
    assert not rig.display.settle(timeout=0.05)
    rig.controller.gate.set()
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.kinds() == ['full']


def test_reports_on_callers_thread(rig: Rig) -> None:
    """Reports reach `report` from `show`, on its thread, never settle."""
    threads: list[int] = []
    rig.display.stop()
    display = PanelDisplay(
        controller=rig.controller,
        report=lambda refresh: threads.append(threading.get_ident()),
        clock=rig.clock,
    )
    rig.display = display
    display.show(frame=white())
    assert display.settle(timeout=WAIT)
    assert not threads
    caller = threading.Thread(target=lambda: display.show(frame=white()))
    caller.start()
    caller.join(timeout=WAIT)
    assert threads == [caller.ident]


def test_show_copies_the_frame(rig: Rig) -> None:
    """Changing the caller's array after `show` changes nothing drawn."""
    frame = painted(boxes=(BOX_A,), grey=0)
    kept = frame.copy()
    rig.controller.gate.clear()
    rig.display.show(frame=white())
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.show(frame=frame)
    frame[:] = 0
    rig.controller.gate.set()
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.calls[1:] == [
        ('partial', (BOX_A, mono_patch(frame=kept, box=BOX_A))),
    ]


def test_stop_closes_the_controller(rig: Rig) -> None:
    """Stopping ends the worker and closes the controller."""
    rig.show(frame=white())
    rig.display.stop()
    assert rig.controller.closed


def test_stop_leaves_a_stuck_controller_open(
    rig: Rig,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker stuck in a draw is left running, the frame waiting dropped."""
    monkeypatch.setattr(panel_display, 'STOP_WAIT', 0.05)
    rig.controller.gate.clear()
    rig.display.show(frame=white())
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.show(frame=painted(boxes=(BOX_A,), grey=0))
    with caplog.at_level(logging.WARNING):
        rig.display.stop()
    assert not rig.controller.closed
    assert len(caplog.records) == 1
    monkeypatch.setattr(panel_display, 'STOP_WAIT', WAIT)
    rig.controller.gate.set()
    rig.display.stop()
    assert rig.controller.closed
    assert rig.controller.kinds() == ['full']


def flicker(
    *,
    rig: Rig,
    count: int,
    quick: bool = False,
    turned: bool = False,
) -> NDArray[np.uint8]:
    """
    Show frames that alternate black and white in one box.

    The first one is black, so each one is a change from a white frame.

    Parameters
    ----------
    rig : Rig
        The rig.
    count : int
        How many frames to show.
    quick : bool
        Whether each one is quick.
    turned : bool
        Whether each one is turned.

    Returns
    -------
    NDArray[np.uint8]
        The last frame shown.
    """
    frame = white()
    for index in range(count):
        frame = painted(boxes=(BOX_A,), grey=255 if index % 2 else 0)
        rig.show(frame=frame, quick=quick, turned=turned)
    return frame


def test_grey_change_draws_quick_patch(rig: Rig) -> None:
    """A grey change that is not quick is still one quick patch."""
    frame = painted(boxes=(BOX_A,), grey=85)
    rig.show(frame=white())
    rig.show(frame=frame)
    assert rig.controller.calls[1:] == [
        ('partial', (BOX_A, mono_patch(frame=frame, box=BOX_A))),
    ]


def test_patch_drives_only_changed_pixels(rig: Rig) -> None:
    """The old and new bits differ only at the changed pixel."""
    first = painted(boxes=(BOX_A,), grey=170)
    second = first.copy()
    second[12, 20] = 0
    rig.show(frame=first)
    rig.show(frame=second)
    box = Box(left=16, top=12, right=24, bottom=13)
    patch = mono_patch(frame=second, box=box)
    assert rig.controller.calls[1:] == [('partial', (box, patch))]
    [old] = rig.controller.olds
    flipped = sum(
        (a ^ b).bit_count() for a, b in zip(old, patch, strict=True)
    )
    assert flipped == 1


def test_cleans_after_thirty_patches_when_quiet(rig: Rig) -> None:
    """The thirtieth patch is followed by a full refresh of its frame."""
    rig.show(frame=white())
    last = flicker(rig=rig, count=CLEAN_AFTER)
    rig.show(frame=last)
    assert rig.controller.kinds() == [
        'full', *['partial'] * CLEAN_AFTER, 'full',
    ]
    assert rig.controller.calls[-1] == ('full', grey_planes(frame=last))
    assert [r.full for r in rig.reports][-2:] == [False, True]


def test_twenty_nine_patches_do_not_clean(rig: Rig) -> None:
    """One patch short of the count, nothing is cleaned."""
    rig.show(frame=white())
    last = flicker(rig=rig, count=CLEAN_AFTER - 1)
    rig.clock.now += CLEAN_QUIET
    rig.show(frame=last)
    assert rig.controller.kinds() == ['full', *['partial'] * 29]


def test_no_clean_within_quiet_time(rig: Rig) -> None:
    """A knob turn holds the clean back for the quiet time."""
    rig.show(frame=white())
    flicker(rig=rig, count=CLEAN_AFTER - 1)
    last = painted(boxes=(BOX_A,), grey=255)
    rig.show(frame=last, quick=True)
    rig.clock.now += CLEAN_QUIET - 1
    rig.show(frame=last)
    assert rig.controller.kinds() == ['full', *['partial'] * 30]
    rig.clock.now += 1
    rig.show(frame=last)
    assert rig.controller.kinds() == ['full', *['partial'] * 30, 'full']


def test_full_resets_patch_count(rig: Rig) -> None:
    """Patches before a full refresh do not count towards a clean."""
    rig.show(frame=white())
    flicker(rig=rig, count=20)
    rig.show(frame=white(), full=True)
    flicker(rig=rig, count=20)
    assert rig.controller.kinds() == [
        'full', *['partial'] * 20, 'full', *['partial'] * 20,
    ]


def test_turned_frame_resets_patch_count(rig: Rig) -> None:
    """Turning the screen is a full refresh that restarts the count."""
    rig.show(frame=white())
    flicker(rig=rig, count=20)
    rig.show(frame=white(), turned=True)
    flicker(rig=rig, count=20, turned=True)
    assert rig.controller.kinds() == [
        'full', *['partial'] * 20, 'full', *['partial'] * 20,
    ]


def test_owed_full_resets_patch_count(rig: Rig) -> None:
    """The full refresh owed after a failure restarts the count."""
    rig.show(frame=white())
    flicker(rig=rig, count=25)
    rig.controller.failures.append(OSError('spi'))
    rig.show(frame=white())
    rig.show(frame=white())
    flicker(rig=rig, count=20)
    assert rig.controller.kinds() == [
        'full', *['partial'] * 26, 'full', *['partial'] * 20,
    ]


def test_failed_clean_owes_a_full(rig: Rig) -> None:
    """A failed clean is not retried; the next frame is full."""
    rig.show(frame=white())
    flicker(rig=rig, count=CLEAN_AFTER, quick=True)
    rig.controller.failures.append(OSError('spi'))
    rig.clock.now += CLEAN_QUIET
    rig.show(frame=white())
    rig.show(frame=white())
    rig.show(frame=white())
    assert rig.controller.kinds() == [
        'full', *['partial'] * CLEAN_AFTER, 'full', 'full',
    ]
    assert [(r.full, r.ok) for r in rig.reports][-2:] == [
        (True, False),
        (True, True),
    ]


@pytest.mark.parametrize('attempt', range(20))
def test_worker_cleans_on_its_own_wake_up(
    monkeypatch: pytest.MonkeyPatch, attempt: int
) -> None:
    """A clean falls due from the worker's timed wake-up."""
    assert attempt >= 0
    monkeypatch.setattr(panel_display, 'CLEAN_QUIET', 0.01)
    controller = GatedController()
    display = PanelDisplay(
        controller=controller,
        report=lambda refresh: None,
        clock=CreepingClock(),
    )
    display.show(frame=white())
    assert display.settle(timeout=WAIT)
    for index in range(CLEAN_AFTER):
        grey = 255 if index % 2 else 0
        display.show(frame=painted(boxes=(BOX_A,), grey=grey), quick=True)
        assert display.settle(timeout=WAIT)
    deadline = time.monotonic() + WAIT
    while len(controller.calls) <= CLEAN_AFTER + 1:
        assert time.monotonic() < deadline
        time.sleep(0.001)
    assert display.settle(timeout=WAIT)
    display.stop()
    assert controller.kinds() == ['full', *['partial'] * CLEAN_AFTER, 'full']


class Unsettled(FrameRecorder):
    """A display that never settles."""

    def settle(self, *, timeout: float) -> bool:
        """
        Report that frames are still on their way.

        Parameters
        ----------
        timeout : float
            Ignored.

        Returns
        -------
        bool
            Always False.
        """
        del timeout
        return False


def test_displays_fans_out() -> None:
    """Every display gets each frame; settled only if all are."""
    first = FrameRecorder()
    second = Unsettled()
    frame = white()
    displays = Displays(displays=[first, second])
    displays.show(frame=frame, full=True, turned=True)
    assert first.frames == second.frames == [frame]
    assert first.fulls == second.fulls == [True]
    assert first.turns == second.turns == [True]
    assert not displays.settle(timeout=0)
    assert Displays(displays=[first]).settle(timeout=0)
