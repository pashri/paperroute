"""Tests for opening the knobs for the service."""

import logging

import pytest

from paperroute_hardware.knobs_open import knobs_for, knobs_state_for
from paperroute_hardware.ports import KnobLines
from paperroute_testkit.hardware_fakes import FakeKnobLines


def test_knobs_for_off_never_opens() -> None:
    """Without --knobs the opener is not called."""
    opened: list[str] = []

    def opener() -> KnobLines:
        opened.append('opened')
        raise OSError('unused')

    assert knobs_for(
        enabled=False, opener=opener, swapped=lambda: False,
    ) is None
    assert not opened


def test_knobs_for_opens_a_reader() -> None:
    """Lines that open give a reader, which releases them on stop."""
    lines = FakeKnobLines()
    reader = knobs_for(enabled=True, opener=lambda: lines, swapped=bool)
    assert reader is not None
    reader.stop()
    assert lines.closes == 1


@pytest.mark.parametrize('error', [OSError('busy'), ImportError('gpiod')])
def test_knobs_for_failure_runs_without_knobs(
    caplog: pytest.LogCaptureFixture, error: Exception,
) -> None:
    """Lines that will not open are one error log and no knobs."""

    def opener() -> KnobLines:
        raise error

    with caplog.at_level(logging.ERROR):
        reader = knobs_for(enabled=True, opener=opener, swapped=bool)
    assert reader is None
    assert [r.getMessage() for r in caplog.records] == [
        'knobs not available',
    ]


class _DeadLines(FakeKnobLines):
    """Lines that open but cannot be read."""

    def level(self, *, line: int) -> bool:
        """Fail as a dead line does."""
        raise OSError(f'line {line} gone')


def test_knobs_for_unreadable_lines_run_without_knobs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lines that fail their first read are released, with no knobs."""
    lines = _DeadLines()
    with caplog.at_level(logging.ERROR):
        reader = knobs_for(enabled=True, opener=lambda: lines, swapped=bool)
    assert reader is None
    assert lines.closes == 1
    assert [r.getMessage() for r in caplog.records] == [
        'knobs not available',
    ]


def test_knobs_state_for() -> None:
    """None when not asked for, else whether the lines opened."""
    lines = FakeKnobLines()
    reader = knobs_for(enabled=True, opener=lambda: lines, swapped=bool)
    assert knobs_state_for(wanted=False, knobs=None) is None
    assert knobs_state_for(wanted=True, knobs=None) is False
    assert knobs_state_for(wanted=True, knobs=reader) is True
    assert reader is not None
    reader.stop()


def test_knobs_state_for_knobs_that_stop_answering() -> None:
    """Knobs whose lines fail while running read as not answering."""
    lines = FakeKnobLines(error=OSError('gone'))
    reader = knobs_for(enabled=True, opener=lambda: lines, swapped=bool)
    assert reader is not None
    reader.run(post=lambda event: None)
    assert knobs_state_for(wanted=True, knobs=reader) is False
    reader.stop()
