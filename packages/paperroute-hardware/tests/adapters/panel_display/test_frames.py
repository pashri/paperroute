"""Tests for panel display: frames."""

import logging
import threading

import numpy as np
import pytest

from paperroute_hardware.adapters import panel_display
from paperroute_hardware.adapters.epaper import PanelTimeout
from paperroute_hardware.adapters.panel_display import PanelDisplay
from paperroute_hardware.render.panel_bits import (
    Box,
    grey_planes,
    mono_patch,
    quick_planes,
)
from tests.adapters.panel_display.helpers import (
    BOX_A,
    BOX_B,
    WAIT,
    Rig,
    painted,
    white,
)


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
        opener=lambda: rig.controller,
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
