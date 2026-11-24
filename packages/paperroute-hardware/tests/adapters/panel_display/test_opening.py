"""Tests for panel display: opening."""

from paperroute_device_testkit.hardware_fakes import FrameRecorder
from paperroute_hardware.adapters.panel_display import Displays, PanelDisplay
from paperroute_hardware.ports import PanelRefresh
from tests.adapters.panel_display.helpers import (
    WAIT,
    GatedController,
    show_settle_show,
    white,
)


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


def test_displays_passes_asleep() -> None:
    """Displays forwards asleep and clear to each display."""
    first = FrameRecorder()
    second = FrameRecorder()
    displays = Displays(displays=[first, second])
    displays.show(frame=white(), asleep=True, clear=True)
    assert first.sleeps == second.sleeps == [True]
    assert first.clears == second.clears == [True]


def test_displays_passes_clean_after() -> None:
    """Displays forwards the cleaning count to each display."""
    first = FrameRecorder()
    second = FrameRecorder()
    displays = Displays(displays=[first, second])
    displays.show(frame=white(), clean_after=120)
    assert first.cleans == second.cleans == [120]


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


def test_panel_opens_on_first_job() -> None:
    """Nothing opens at start-up; the first frame opens the panel."""
    opened: list[str] = []
    controller = GatedController()

    def opener() -> GatedController:
        opened.append('open')
        return controller

    display = PanelDisplay(opener=opener, report=lambda refresh: None)
    try:
        assert not opened
        display.show(frame=white())
        assert display.settle(timeout=WAIT)
        assert opened == ['open']
        display.show(frame=white(), full=True)
        assert display.settle(timeout=WAIT)
        assert opened == ['open']
    finally:
        display.stop()
    assert controller.closed


def test_stop_without_an_open_panel_closes_nothing() -> None:
    """Stopping a display that never drew does not open the panel."""
    opened: list[str] = []

    def opener() -> GatedController:
        opened.append('open')
        return GatedController()

    PanelDisplay(opener=opener, report=lambda refresh: None).stop()
    assert not opened


def test_failed_open_retries_on_next_job() -> None:
    """A failed open is reported, then the next frame is a full refresh."""
    controller = GatedController()
    attempts: list[str] = []

    def opener() -> GatedController:
        attempts.append('try')
        if len(attempts) == 1:
            raise OSError('no spi')
        return controller

    reports: list[PanelRefresh] = []
    display = PanelDisplay(opener=opener, report=reports.append)
    try:
        display.show(frame=white())
        assert display.settle(timeout=WAIT)
        assert not controller.calls
        show_settle_show(display=display)
    finally:
        display.stop()
    assert [kind for kind, _ in controller.calls] == ['full']
    assert [refresh.ok for refresh in reports] == [False, True]
    assert len(attempts) == 2
