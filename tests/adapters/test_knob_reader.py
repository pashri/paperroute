"""Tests for reading knob events off the GPIO lines."""

import logging
import time
from collections.abc import Callable, Iterator

import pytest

from paperroute.adapters.knob_reader import KnobReader
from paperroute.core.knob_decoder import KnobDecoder
from paperroute.core.knob_hold import HOLD_S
from paperroute.core.messages import (
    Event,
    Knob,
    KnobHeld,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute.ports import LineEdge
from tests.fakes import FakeKnobLines

A_CLK, A_DT, A_SW = 20, 21, 26
B_CLK, B_DT, B_SW = 5, 6, 16


def _clock(*, times: list[float]) -> Callable[[], float]:
    """A clock giving each time in turn, then the last one."""
    ticks: Iterator[float] = iter(times)
    last = [times[0]]

    def now() -> float:
        last[0] = next(ticks, last[0])
        return last[0]

    return now


def _reader(
    *,
    lines: FakeKnobLines,
    times: list[float] | None = None,
    swap: bool = False,
) -> KnobReader:
    """A reader over fake lines that never waits."""
    return KnobReader(
        lines=lines,
        decoder=KnobDecoder(swapped=lambda: swap),
        clock=_clock(times=times or [0.0]),
        wait=0.0,
    )


@pytest.mark.parametrize(('clk', 'dt', 'knob'), [
    (A_CLK, A_DT, Knob.TOP),
    (B_CLK, B_DT, Knob.BOTTOM),
])
@pytest.mark.parametrize(('high', 'steps'), [(True, 1), (False, -1)])
def test_step_clk_fall_reads_dt_for_direction(
    clk: int, dt: int, knob: Knob, high: bool, steps: int,
) -> None:
    """CLK falling with DT high is a turn right; DT low, left."""
    lines = FakeKnobLines(
        reads=[[LineEdge(line=clk, rising=False)]], levels={dt: high},
    )
    assert _reader(lines=lines).step() == [KnobTurned(knob=knob, steps=steps)]


def test_step_ignores_clk_rises_and_dt_edges() -> None:
    """Only a CLK fall counts as a detent."""
    lines = FakeKnobLines(reads=[[
        LineEdge(line=A_CLK, rising=True),
        LineEdge(line=A_DT, rising=False),
        LineEdge(line=A_DT, rising=True),
        LineEdge(line=B_CLK, rising=True),
    ]])
    assert not _reader(lines=lines).step()


def test_step_switch_fall_then_rise_is_a_press() -> None:
    """The switch pulls its line low while pressed."""
    lines = FakeKnobLines(reads=[
        [LineEdge(line=A_SW, rising=False)],
        [LineEdge(line=A_SW, rising=True)],
    ])
    reader = _reader(lines=lines, times=[0.0, 0.2])
    assert not reader.step()
    assert reader.step() == [KnobPressed(knob=Knob.TOP)]


def test_step_switch_held_past_the_hold_time_is_a_hold() -> None:
    """A release after the hold time is a hold."""
    lines = FakeKnobLines(reads=[
        [LineEdge(line=B_SW, rising=False)],
        [],
        [LineEdge(line=B_SW, rising=True)],
    ])
    reader = _reader(lines=lines, times=[0.0, 0.5, HOLD_S + 0.1])
    assert not reader.step()
    assert not reader.step()
    assert reader.step() == [KnobHeld(knob=Knob.BOTTOM)]


def test_step_polls_both_held_with_no_edges() -> None:
    """KnobsHeld comes while both are still down, on a quiet read."""
    lines = FakeKnobLines(reads=[[
        LineEdge(line=A_SW, rising=False),
        LineEdge(line=B_SW, rising=False),
    ]])
    reader = _reader(lines=lines, times=[0.0, HOLD_S + 0.1, 2.0])
    assert not reader.step()
    assert reader.step() == [KnobsHeld()]
    assert not reader.step()


def test_step_swapped_turns_a_into_the_bottom_knob() -> None:
    """With the swap on, knob A's turn is the bottom knob's."""
    lines = FakeKnobLines(reads=[[LineEdge(line=A_CLK, rising=False)]])
    assert _reader(lines=lines, swap=True).step() == [
        KnobTurned(knob=Knob.BOTTOM, steps=1),
    ]


def test_start_posts_events_until_stopped() -> None:
    """The thread posts each event; stop closes the lines once."""
    lines = FakeKnobLines(reads=[[LineEdge(line=B_CLK, rising=False)]])
    reader = KnobReader(
        lines=lines, decoder=KnobDecoder(swapped=lambda: False), wait=0.01,
    )
    posted: list[Event] = []
    reader.start(post=posted.append)
    deadline = time.monotonic() + 2
    while not posted and time.monotonic() < deadline:
        time.sleep(0.01)
    reader.stop()
    reader.stop()
    assert posted == [KnobTurned(knob=Knob.BOTTOM, steps=1)]
    assert lines.closes == 1


def test_stop_without_start_closes_the_lines() -> None:
    """Lines opened but never read are still released."""
    lines = FakeKnobLines()
    _reader(lines=lines).stop()
    assert lines.closes == 1


def test_run_logs_a_line_error_and_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A read that fails ends the loop with one error logged."""
    lines = FakeKnobLines(error=OSError('gone'))
    posted: list[Event] = []
    with caplog.at_level(logging.ERROR):
        _reader(lines=lines).run(post=posted.append)
    assert not posted
    assert [r.getMessage() for r in caplog.records] == [
        'knobs stopped answering',
    ]
