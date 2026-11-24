"""Tests for panel display: splash."""

import errno
import logging
import time
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_hardware.adapters import panel_display
from paperroute_hardware.adapters.epaper import PanelTimeout
from paperroute_hardware.adapters.panel_display import PanelDisplay
from paperroute_hardware.ports import PanelRefresh
from paperroute_hardware.render.panel_bits import grey_planes, oriented
from tests.adapters.panel_display.helpers import (
    BOX_A,
    QUICK_RETRY,
    WAIT,
    GatedController,
    Rig,
    painted,
    show_settle_show,
    white,
)


def picture_file(*, path: Path) -> tuple[Path, NDArray[np.uint8]]:
    """
    Write a raw splash picture.

    Parameters
    ----------
    path : Path
        Where to write it.

    Returns
    -------
    tuple[Path, NDArray[np.uint8]]
        The path and the frame the file holds.
    """
    picture = painted(boxes=(BOX_A,), grey=85)
    path.write_bytes(picture.tobytes())
    return path, picture


def test_splash_is_drawn_full_before_any_frame(
    rig: Rig, tmp_path: Path,
) -> None:
    """A splash asked for first is drawn with a full refresh."""
    image, picture = picture_file(path=tmp_path / 'on.raw')
    rig.display.splash(image=image, turned=False)
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.calls == [('full', grey_planes(frame=picture))]


def test_turned_splash_is_drawn_turned(rig: Rig, tmp_path: Path) -> None:
    """A turned splash is drawn upside down."""
    image, picture = picture_file(path=tmp_path / 'on.raw')
    rig.display.splash(image=image, turned=True)
    assert rig.display.settle(timeout=WAIT)
    turned = oriented(frame=picture, turned=True)
    assert rig.controller.calls == [('full', grey_planes(frame=turned))]


def test_first_frame_after_splash_is_full(rig: Rig, tmp_path: Path) -> None:
    """The first frame after a splash is full; the splash is not reported."""
    image, _ = picture_file(path=tmp_path / 'on.raw')
    rig.display.splash(image=image, turned=False)
    assert rig.display.settle(timeout=WAIT)
    rig.show(frame=white())
    rig.show(frame=white())
    assert rig.controller.calls[1:] == [('full', grey_planes(frame=white()))]
    assert [(r.full, r.ok) for r in rig.reports] == [(True, True)]


def test_show_never_waits_for_a_splash(rig: Rig, tmp_path: Path) -> None:
    """A frame shown during a splash returns at once and is drawn after."""
    image, _ = picture_file(path=tmp_path / 'on.raw')
    rig.controller.gate.clear()
    rig.display.splash(image=image, turned=False)
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.show(frame=white())
    assert not rig.display.settle(timeout=0.05)
    rig.controller.gate.set()
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.calls[1:] == [('full', grey_planes(frame=white()))]


def test_splash_after_a_frame_is_ignored(rig: Rig, tmp_path: Path) -> None:
    """Once a frame was shown, a splash draws nothing."""
    image, _ = picture_file(path=tmp_path / 'on.raw')
    rig.show(frame=white())
    rig.display.splash(image=image, turned=False)
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.kinds() == ['full']


@pytest.mark.parametrize(
    'content',
    [None, b'\x00' * 10],
    ids=['missing', 'short'],
)
def test_unreadable_splash_is_skipped_without_opening(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    content: bytes | None,
) -> None:
    """A missing or short splash file is logged and nothing opens."""
    image = tmp_path / 'on.raw'
    if content is not None:
        image.write_bytes(content)
    opened: list[str] = []

    def opener() -> GatedController:
        opened.append('open')
        return GatedController()

    display = PanelDisplay(opener=opener, report=lambda refresh: None)
    try:
        with caplog.at_level(logging.INFO):
            display.splash(image=image, turned=False)
            assert display.settle(timeout=WAIT)
    finally:
        display.stop()
    assert not opened
    assert [
        (record.levelno, record.message.startswith('splash skipped: '))
        for record in caplog.records
    ] == [(logging.INFO, True)]


def test_busy_panel_skips_the_splash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A busy panel at the splash costs nothing but a log line."""
    image, _ = picture_file(path=tmp_path / 'on.raw')
    controller = GatedController()
    attempts: list[str] = []

    def opener() -> GatedController:
        attempts.append('try')
        if len(attempts) == 1:
            raise OSError(errno.EBUSY, 'busy')
        return controller

    reports: list[PanelRefresh] = []
    display = PanelDisplay(opener=opener, report=reports.append)
    try:
        with caplog.at_level(logging.INFO):
            display.splash(image=image, turned=False)
            assert display.settle(timeout=WAIT)
        show_settle_show(display=display)
    finally:
        display.stop()
    assert controller.calls == [('full', grey_planes(frame=white()))]
    assert len(attempts) == 2
    assert [refresh.ok for refresh in reports] == [True]
    assert [record.levelno for record in caplog.records] == [logging.INFO]


@pytest.mark.parametrize(
    'error',
    [PanelTimeout('busy'), RuntimeError('bug')],
    ids=['timeout', 'bug'],
)
def test_failed_splash_is_not_retried(
    *,
    rig: Rig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """A splash that fails is dropped: no retry, no report."""
    image, _ = picture_file(path=tmp_path / 'on.raw')
    monkeypatch.setattr(panel_display, 'RETRY_AFTER', QUICK_RETRY)
    rig.controller.failures.append(error)
    with caplog.at_level(logging.INFO):
        rig.display.splash(image=image, turned=False)
        assert rig.display.settle(timeout=WAIT)
        rig.clock.now += QUICK_RETRY
        time.sleep(2 * QUICK_RETRY)
        assert rig.display.settle(timeout=WAIT)
        assert rig.controller.kinds() == ['full']
        rig.show(frame=white())
    assert rig.controller.calls[1:] == [('full', grey_planes(frame=white()))]
    assert [record.levelno for record in caplog.records] == [logging.INFO]


def test_frame_drops_a_splash_not_yet_started(
    rig: Rig, tmp_path: Path,
) -> None:
    """A frame shown before the worker starts a splash replaces it."""
    image, picture = picture_file(path=tmp_path / 'on.raw')
    rig.controller.gate.clear()
    rig.display.splash(image=image, turned=False)
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.splash(image=image, turned=True)
    rig.display.show(frame=white())
    rig.controller.gate.set()
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.calls == [
        ('full', grey_planes(frame=picture)),
        ('full', grey_planes(frame=white())),
    ]


def test_settle_after_stop_with_a_splash_waiting(
    rig: Rig, tmp_path: Path,
) -> None:
    """Stopping drops a splash the worker has not started."""
    image, _ = picture_file(path=tmp_path / 'on.raw')
    rig.controller.gate.clear()
    rig.display.splash(image=image, turned=False)
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.splash(image=image, turned=True)
    rig.controller.gate.set()
    rig.display.stop()
    assert rig.display.settle(timeout=0.05)
