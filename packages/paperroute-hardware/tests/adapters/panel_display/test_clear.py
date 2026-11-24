"""Tests for panel display: clear."""

from paperroute_hardware.adapters.panel_display import CLEAN_QUIET, SCREEN
from paperroute_hardware.render.panel_bits import mono_patch
from tests.adapters.panel_display.helpers import (
    BOX_A,
    BOX_B,
    WAIT,
    Rig,
    flicker,
    painted,
    white,
)


def test_clear_job_draws_clear(rig: Rig) -> None:
    """A clear frame is one clear refresh of its whole-screen bits."""
    rig.show(frame=white())
    frame = painted(boxes=(BOX_A, BOX_B), grey=0)
    rig.show(frame=frame, clear=True)
    assert rig.controller.kinds() == ['full', 'clear']
    assert rig.controller.calls[-1] == (
        'clear', mono_patch(frame=frame, box=SCREEN),
    )
    assert [r.full for r in rig.reports[:1]] == [True]
    rig.show(frame=white())
    assert rig.reports[-1].full is True


def test_clear_refresh_reports_clear(rig: Rig) -> None:
    """A clear refresh is reported as a clear, timed as a full one."""
    rig.show(frame=white())
    rig.show(frame=painted(boxes=(BOX_A,), grey=0), clear=True)
    rig.show(frame=white())
    assert [(r.full, r.clear) for r in rig.reports[:2]] == [
        (True, False),
        (True, True),
    ]


def test_forced_full_clear_reports_full(rig: Rig) -> None:
    """A clear forced to full is reported as full, not as a clear."""
    rig.show(frame=white(), clear=True)
    rig.show(frame=white())
    assert (rig.reports[0].full, rig.reports[0].clear) == (True, False)


def test_clear_resets_patch_count(rig: Rig) -> None:
    """Patches before a clear refresh do not count towards a clean."""
    rig.show(frame=white())
    flicker(rig=rig, count=20)
    rig.show(frame=white(), clear=True)
    last = flicker(rig=rig, count=20)
    rig.clock.now += CLEAN_QUIET
    rig.show(frame=last)
    assert rig.controller.kinds().count('full') == 1


def test_owed_full_beats_clear(rig: Rig) -> None:
    """The first frame is owed a full refresh, even when asked clear."""
    rig.show(frame=white(), clear=True)
    assert rig.controller.kinds() == ['full']


def test_asked_full_beats_clear(rig: Rig) -> None:
    """A full refresh asked for wins over a clear."""
    rig.show(frame=white())
    rig.show(frame=painted(boxes=(BOX_A,), grey=0), full=True, clear=True)
    assert rig.controller.kinds() == ['full', 'full']


def test_show_keeps_clear_through_replacement(rig: Rig) -> None:
    """A waiting clear frame replaced by a newer one stays clear."""
    rig.controller.gate.clear()
    rig.display.show(frame=white())
    assert rig.controller.entered.wait(timeout=WAIT)
    rig.display.show(frame=painted(boxes=(BOX_B,), grey=0), clear=True)
    rig.display.show(frame=painted(boxes=(BOX_A, BOX_B), grey=0))
    rig.controller.gate.set()
    assert rig.display.settle(timeout=WAIT)
    assert rig.controller.kinds() == ['full', 'clear']
