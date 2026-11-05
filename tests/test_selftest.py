"""Tests for the panel self-test."""

import io

import numpy as np

from paperroute.render.panel_bits import Box
from paperroute.selftest import card, run_selftest


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

    def draw_partial(self, *, box: Box, patch: bytes) -> None:
        """Record a partial refresh."""
        assert patch
        if self.fail:
            raise TimeoutError('busy stuck')
        self.draws.append(('partial', box))
        self.now += self.partial_seconds

    def draw_grey_patch(
        self, *, box: Box, planes: tuple[bytes, bytes],
    ) -> None:
        """Record a grey patch."""
        assert planes[0]
        self.draws.append(('grey', box))
        self.now += 2.1

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
    """One full, ten marching partials, one grey patch, then PASS."""
    controller = RecordingController()
    out = io.StringIO()
    status = run_selftest(
        controller=controller, out=out, clock=lambda: controller.now,
    )
    assert status == 0
    kinds = [kind for kind, _ in controller.draws]
    assert kinds == ['full', *['partial'] * 10, 'grey']
    assert controller.draws[1][1] == Box(40, 400, 112, 440)
    assert controller.draws[10][1] == Box(40 + 72 * 9, 400, 760, 440)
    assert controller.draws[-1][1] == Box(40, 400, 760, 440)
    lines = out.getvalue().splitlines()
    assert lines[0] == 'full: 4.7 s'
    assert lines[1] == 'partial 1: 0.50 s'
    assert lines[-2] == 'grey patch: 2.10 s'
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
