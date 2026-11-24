"""Tests for panel display: retry and sleep."""

import logging
import time

import pytest

from paperroute_hardware.adapters import panel_display
from paperroute_hardware.adapters.epaper import PanelTimeout
from paperroute_hardware.adapters.panel_display import CLEAN_QUIET, RETRY_AFTER
from paperroute_hardware.ports import CLEAN_AFTER
from paperroute_hardware.render.panel_bits import grey_planes
from tests.adapters.panel_display.helpers import (
    BOX_A,
    BOX_B,
    QUICK_RETRY,
    WAIT,
    Rig,
    flicker,
    painted,
    white,
)


def wait_for_calls(*, rig: Rig, count: int) -> None:
    """
    Wait until the controller has had a number of calls, then settle.

    Parameters
    ----------
    rig : Rig
        The rig.
    count : int
        How many calls to wait for.
    """
    deadline = time.monotonic() + WAIT
    while len(rig.controller.calls) < count:
        assert time.monotonic() < deadline
        time.sleep(0.001)
    assert rig.display.settle(timeout=WAIT)


def test_failed_refresh_is_retried_after_a_minute(
    rig: Rig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed frame is redrawn full after each wait until it works."""
    monkeypatch.setattr(panel_display, 'RETRY_AFTER', QUICK_RETRY)
    frame = painted(boxes=(BOX_A,), grey=0)
    rig.show(frame=white())
    rig.controller.failures.extend((OSError('spi'), PanelTimeout('busy')))
    with caplog.at_level(logging.ERROR):
        rig.show(frame=frame)
        rig.clock.now += QUICK_RETRY
        wait_for_calls(rig=rig, count=3)
        rig.clock.now += QUICK_RETRY
        wait_for_calls(rig=rig, count=4)
    assert rig.controller.kinds() == ['full', 'partial', 'full', 'full']
    assert rig.controller.calls[-1] == ('full', grey_planes(frame=frame))
    assert [record.message for record in caplog.records] == [
        'panel refresh failed',
    ]


def test_retry_of_asleep_frame_sleeps_after(
    rig: Rig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retried Sleep mode frame puts the panel to sleep once drawn."""
    monkeypatch.setattr(panel_display, 'RETRY_AFTER', QUICK_RETRY)
    rig.show(frame=white())
    rig.controller.failures.append(PanelTimeout('busy'))
    rig.show(frame=painted(boxes=(BOX_B,), grey=0), asleep=True)
    assert rig.controller.kinds() == ['full', 'partial']
    rig.clock.now += QUICK_RETRY
    wait_for_calls(rig=rig, count=4)
    assert rig.controller.kinds() == ['full', 'partial', 'full', 'sleep']


def test_newer_frame_beats_retry(rig: Rig) -> None:
    """A frame shown once a retry is due is drawn full instead."""
    newer = painted(boxes=(BOX_B,), grey=0)
    rig.show(frame=white())
    rig.controller.failures.append(OSError('spi'))
    rig.show(frame=painted(boxes=(BOX_A,), grey=0))
    rig.clock.now += RETRY_AFTER
    rig.show(frame=newer)
    assert rig.controller.kinds() == ['full', 'partial', 'full']
    assert rig.controller.calls[-1] == ('full', grey_planes(frame=newer))


def test_no_retry_before_a_minute(
    rig: Rig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed frame is not redrawn before the wait has passed."""
    monkeypatch.setattr(panel_display, 'RETRY_AFTER', QUICK_RETRY)
    rig.show(frame=white())
    rig.controller.failures.append(OSError('spi'))
    rig.show(frame=painted(boxes=(BOX_A,), grey=0))
    rig.clock.now += QUICK_RETRY / 2
    time.sleep(QUICK_RETRY * 4)
    assert rig.controller.kinds() == ['full', 'partial']
    rig.clock.now += QUICK_RETRY / 2
    wait_for_calls(rig=rig, count=3)
    assert rig.controller.kinds() == ['full', 'partial', 'full']


def test_asleep_frame_puts_panel_to_sleep(rig: Rig) -> None:
    """A frame shown asleep is drawn, then the panel deep-sleeps."""
    rig.show(frame=white(), full=True, asleep=True)
    assert rig.controller.kinds() == ['full', 'sleep']


def test_asleep_patch_puts_panel_to_sleep(rig: Rig) -> None:
    """A patch shown asleep is followed by a deep sleep too."""
    rig.show(frame=white())
    rig.show(frame=painted(boxes=(BOX_A,), grey=0), asleep=True)
    assert rig.controller.kinds() == ['full', 'partial', 'sleep']


def test_awake_frames_never_sleep(rig: Rig) -> None:
    """Frames shown awake never put the panel to sleep."""
    rig.show(frame=white())
    flicker(rig=rig, count=3)
    rig.show(frame=white(), full=True)
    assert 'sleep' not in rig.controller.kinds()


def test_waking_frame_draws_without_sleep(rig: Rig) -> None:
    """After Sleep mode, an awake frame is drawn and not slept."""
    rig.show(frame=white(), full=True, asleep=True)
    rig.show(frame=painted(boxes=(BOX_A,), grey=0), full=True)
    assert rig.controller.kinds() == ['full', 'sleep', 'full']


def test_no_clean_while_asleep(rig: Rig) -> None:
    """A clean that falls due in Sleep mode does not wake the panel."""
    rig.show(frame=white())
    flicker(rig=rig, count=CLEAN_AFTER, quick=True)
    asleep = painted(boxes=(BOX_B,), grey=0)
    rig.show(frame=asleep, asleep=True)
    rig.clock.now += CLEAN_QUIET
    rig.show(frame=asleep, asleep=True)
    assert 'full' not in rig.controller.kinds()[1:]
