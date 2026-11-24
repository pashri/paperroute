"""Tests for panel display: patches."""

import time

import pytest

from paperroute_hardware.adapters import panel_display
from paperroute_hardware.adapters.panel_display import (
    CLEAN_QUIET,
    PanelDisplay,
)
from paperroute_hardware.ports import CLEAN_AFTER
from paperroute_hardware.render.panel_bits import Box, grey_planes, mono_patch
from tests.adapters.panel_display.helpers import (
    BOX_A,
    WAIT,
    Clock,
    GatedController,
    Rig,
    flicker,
    painted,
    white,
)


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


def test_cleans_after_fifteen_when_told_fifteen(rig: Rig) -> None:
    """With a count of 15, the fifteenth patch is followed by a clean."""
    rig.show(frame=white(), clean_after=15)
    last = flicker(rig=rig, count=15, clean_after=15)
    rig.show(frame=last, clean_after=15)
    assert rig.controller.kinds() == ['full', *['partial'] * 15, 'full']
    assert rig.controller.calls[-1] == ('full', grey_planes(frame=last))


def test_no_clean_after_thirty_when_told_sixty(rig: Rig) -> None:
    """With a count of 60, thirty patches are not cleaned."""
    rig.show(frame=white(), clean_after=60)
    last = flicker(rig=rig, count=CLEAN_AFTER, clean_after=60)
    rig.clock.now += CLEAN_QUIET
    rig.show(frame=last, clean_after=60)
    assert rig.controller.kinds() == ['full', *['partial'] * CLEAN_AFTER]


def test_lowered_count_cleans_once_quiet(rig: Rig) -> None:
    """A count lowered below the patches drawn cleans after the quiet."""
    rig.show(frame=white())
    flicker(rig=rig, count=20)
    last = painted(boxes=(BOX_A,), grey=0)
    rig.show(frame=last, quick=True, clean_after=15)
    rig.clock.now += CLEAN_QUIET - 1
    rig.show(frame=last, clean_after=15)
    assert rig.controller.kinds() == ['full', *['partial'] * 21]
    rig.clock.now += 1
    rig.show(frame=last, clean_after=15)
    assert rig.controller.kinds() == ['full', *['partial'] * 21, 'full']


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
    """A failed clean owes a full; the next frame is drawn full."""
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
        opener=lambda: controller,
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
