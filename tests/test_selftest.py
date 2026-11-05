"""Tests for the panel self-test."""

import errno
import io

import numpy as np
import pytest

from paperroute.ports import PanelBus
from paperroute.render.panel_bits import Box
from paperroute.selftest import (
    BUSY_MESSAGE,
    NO_PANEL_MESSAGE,
    card,
    run_selftest,
    selftest_main,
)


class RecordingController:
    """A controller that records its draws and advances a fake clock."""

    def __init__(self, *, partial_seconds: float = 0.5) -> None:
        self.now = 0.0
        self.partial_seconds = partial_seconds
        self.draws: list[tuple[str, Box | None]] = []
        self.closed = False
        self.fail = False

    def draw_full(self, *, planes: tuple[bytes, bytes]) -> None:
        """Record a full refresh."""
        assert len(planes[0]) == 48000
        self.draws.append(('full', None))
        self.now += 4.7

    def draw_partial(
        self, *, box: Box, old: bytes, patch: bytes
    ) -> None:
        """Record a partial refresh."""
        assert old and patch
        if self.fail:
            raise TimeoutError('busy stuck')
        self.draws.append(('partial', box))
        self.now += self.partial_seconds

    def sleep(self) -> None:
        """Record a deep sleep."""
        self.draws.append(('sleep', None))

    def close(self) -> None:
        """Record the close."""
        self.closed = True


def test_card_bands_and_bar() -> None:
    """The card has four bands and a white bar in a black outline."""
    frame = card()
    assert frame.shape == (480, 800)
    assert [int(frame[0, x]) for x in (0, 200, 400, 600)] == [
        255, 170, 85, 0,
    ]
    assert np.all(frame[400:440, 40:760] == 255)
    assert np.all(frame[396:400, 36:764] == 0)
    assert np.all(frame[400:440, 36:40] == 0)
    assert np.all(frame[400:440, 760:764] == 0)


def test_run_selftest_sequence_and_pass() -> None:
    """One full and ten marching partials, then PASS."""
    controller = RecordingController()
    out = io.StringIO()
    status = run_selftest(
        controller=controller, out=out, clock=lambda: controller.now,
    )
    assert status == 0
    kinds = [kind for kind, _ in controller.draws]
    assert kinds == ['full', *['partial'] * 10]
    assert controller.draws[1][1] == Box(40, 400, 112, 440)
    assert controller.draws[10][1] == Box(40 + 72 * 9, 400, 760, 440)
    lines = out.getvalue().splitlines()
    assert lines[0] == 'full: 4.7 s'
    assert lines[1] == 'partial 1: 0.50 s'
    assert lines[-2] == 'partial 10: 0.50 s'
    assert not any('grey' in line for line in lines)
    assert lines[-1] == 'PASS'
    assert controller.closed


def test_run_selftest_slow_partial_fails() -> None:
    """A partial over a second fails the self-test."""
    controller = RecordingController(partial_seconds=1.2)
    out = io.StringIO()
    status = run_selftest(
        controller=controller, out=out, clock=lambda: controller.now,
    )
    assert status == 1
    assert out.getvalue().splitlines()[-1] == (
        'FAIL: slowest partial 1.20 s'
    )


def test_run_selftest_raising_controller_fails() -> None:
    """A refresh that raises gives FAIL and 1, and the panel is closed."""
    controller = RecordingController()
    controller.fail = True
    out = io.StringIO()
    status = run_selftest(
        controller=controller, out=out, clock=lambda: controller.now,
    )
    assert status == 1
    assert 'FAIL: TimeoutError: busy stuck' in out.getvalue()
    assert controller.closed


def failing(*, error: Exception) -> PanelBus:
    """
    Fail to open the bus.

    Parameters
    ----------
    error : Exception
        What to raise.

    Raises
    ------
    Exception
        Always `error`.
    """
    raise error


def test_selftest_main_busy_panel(capsys: pytest.CaptureFixture[str]) -> None:
    """A panel in use gives the stop-paperroute message and exit 2."""
    status = selftest_main(
        opener=lambda: failing(error=OSError(errno.EBUSY, 'busy')),
    )
    assert status == 2
    assert capsys.readouterr().err.strip() == BUSY_MESSAGE


def test_selftest_main_other_os_error_is_raised() -> None:
    """Any other OSError is not hidden."""
    with pytest.raises(OSError, match='gone'):
        selftest_main(
            opener=lambda: failing(error=OSError(errno.ENOENT, 'gone')),
        )


def test_selftest_main_no_panel_here(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A machine without the panel library says so and exits 1."""
    status = selftest_main(
        opener=lambda: failing(error=ImportError('gpiod')),
    )
    assert status == 1
    assert capsys.readouterr().err.strip() == NO_PANEL_MESSAGE
