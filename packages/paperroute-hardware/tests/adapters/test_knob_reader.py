"""Tests for reading knob events off the GPIO lines."""

import logging
import time
from collections.abc import Callable, Iterator

import pytest

from paperroute_hardware.adapters.knob_reader import KnobReader
from paperroute_hardware.core.knob_decoder import KnobDecoder
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobInput,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_hardware.core.knob_hold import HOLD_S
from paperroute_hardware.ports import LineEdge
from paperroute_testkit.hardware_fakes import FakeKnobLines

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
        reads=[[LineEdge(line=clk, rising=False, at=0.0)]],
        levels={dt: high},
    )
    assert _reader(lines=lines).step() == [KnobTurned(knob=knob, steps=steps)]


def _detents(*, clk: int, dt: int, right: bool, count: int) -> list[LineEdge]:
    """The edges of `count` quick detents, 2 ms apart, in one batch."""
    first, second = (clk, dt) if right else (dt, clk)
    order = [(first, False), (second, False), (first, True), (second, True)]
    return [
        LineEdge(line=line, rising=rising, at=(detent * 4 + n) * 0.0005)
        for detent in range(count)
        for n, (line, rising) in enumerate(order)
    ]


@pytest.mark.parametrize(('clk', 'dt', 'knob'), [
    (A_CLK, A_DT, Knob.TOP),
    (B_CLK, B_DT, Knob.BOTTOM),
])
@pytest.mark.parametrize(('right', 'steps'), [(True, 1), (False, -1)])
def test_step_fast_spin_uses_dt_at_each_clk_fall(
    clk: int, dt: int, knob: Knob, right: bool, steps: int,
) -> None:
    """Detents read in one batch each take DT from their own CLK fall."""
    edges = _detents(clk=clk, dt=dt, right=right, count=3)
    batch = edges[:-2] if right else edges
    lines = FakeKnobLines(reads=[batch], levels={dt: True})
    reader = _reader(lines=lines)
    lines.levels[dt] = next(
        edge.rising for edge in reversed(batch) if edge.line == dt
    )
    assert lines.levels[dt] is not right
    assert reader.step() == [KnobTurned(knob=knob, steps=steps)] * 3


def test_step_dt_is_tracked_across_batches() -> None:
    """A DT edge in one batch sets the direction of a CLK fall in the next."""
    lines = FakeKnobLines(
        reads=[
            [LineEdge(line=A_DT, rising=False, at=0.0)],
            [
                LineEdge(line=A_CLK, rising=False, at=0.001),
                LineEdge(line=A_DT, rising=True, at=0.002),
            ],
        ],
        levels={A_DT: True},
    )
    reader = _reader(lines=lines)
    lines.levels[A_DT] = True
    assert not reader.step()
    assert reader.step() == [KnobTurned(knob=Knob.TOP, steps=-1)]


def test_step_times_a_hold_by_its_edges() -> None:
    """A press and release read late in one batch keep their own times."""
    lines = FakeKnobLines(reads=[[
        LineEdge(line=A_SW, rising=False, at=0.0),
        LineEdge(line=A_SW, rising=True, at=HOLD_S + 0.1),
    ]])
    reader = _reader(lines=lines, times=[HOLD_S + 0.5])
    assert reader.step() == [KnobHeld(knob=Knob.TOP)]


def test_step_both_held_in_one_late_batch_is_knobs_held() -> None:
    """Both down past the hold time, read late with a release, still count."""
    lines = FakeKnobLines(reads=[[
        LineEdge(line=A_SW, rising=False, at=0.0),
        LineEdge(line=B_SW, rising=False, at=0.1),
        LineEdge(line=A_SW, rising=True, at=1.0),
        LineEdge(line=B_SW, rising=True, at=1.1),
    ]])
    reader = _reader(lines=lines, times=[1.5])
    assert reader.step() == [KnobsHeld()]


def test_step_ignores_clk_rises_and_dt_edges() -> None:
    """Only a CLK fall counts as a detent."""
    lines = FakeKnobLines(reads=[[
        LineEdge(line=A_CLK, rising=True, at=0.0),
        LineEdge(line=A_DT, rising=False, at=0.0),
        LineEdge(line=A_DT, rising=True, at=0.0),
        LineEdge(line=B_CLK, rising=True, at=0.0),
    ]])
    assert not _reader(lines=lines).step()


def test_step_switch_fall_then_rise_is_a_press() -> None:
    """The switch pulls its line low while pressed."""
    lines = FakeKnobLines(reads=[
        [LineEdge(line=A_SW, rising=False, at=0.0)],
        [LineEdge(line=A_SW, rising=True, at=0.2)],
    ])
    reader = _reader(lines=lines, times=[0.0, 0.2])
    assert not reader.step()
    assert reader.step() == [KnobPressed(knob=Knob.TOP)]


def test_step_switch_held_past_the_hold_time_is_a_hold() -> None:
    """A release after the hold time is a hold."""
    lines = FakeKnobLines(reads=[
        [LineEdge(line=B_SW, rising=False, at=0.0)],
        [],
        [LineEdge(line=B_SW, rising=True, at=HOLD_S + 0.1)],
    ])
    reader = _reader(lines=lines, times=[0.0, 0.5, HOLD_S + 0.1])
    assert not reader.step()
    assert not reader.step()
    assert reader.step() == [KnobHeld(knob=Knob.BOTTOM)]


def test_step_polls_both_held_with_no_edges() -> None:
    """KnobsHeld comes while both are still down, on a quiet read."""
    lines = FakeKnobLines(reads=[[
        LineEdge(line=A_SW, rising=False, at=0.0),
        LineEdge(line=B_SW, rising=False, at=0.0),
    ]])
    reader = _reader(lines=lines, times=[0.0, HOLD_S + 0.1, 2.0])
    assert not reader.step()
    assert reader.step() == [KnobsHeld()]
    assert not reader.step()


def test_step_swapped_turns_a_into_the_bottom_knob() -> None:
    """With the swap on, knob A's turn is the bottom knob's."""
    lines = FakeKnobLines(
        reads=[[LineEdge(line=A_CLK, rising=False, at=0.0)]],
    )
    assert _reader(lines=lines, swap=True).step() == [
        KnobTurned(knob=Knob.BOTTOM, steps=1),
    ]


def test_start_posts_events_until_stopped() -> None:
    """The thread posts each event; stop closes the lines once."""
    lines = FakeKnobLines(
        reads=[[LineEdge(line=B_CLK, rising=False, at=0.0)]],
    )
    reader = KnobReader(
        lines=lines, decoder=KnobDecoder(swapped=lambda: False), wait=0.01,
    )
    posted: list[KnobInput] = []
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
    posted: list[KnobInput] = []
    with caplog.at_level(logging.ERROR):
        _reader(lines=lines).run(post=posted.append)
    assert not posted
    assert [r.getMessage() for r in caplog.records] == [
        'knobs stopped answering',
    ]


def test_answering_until_a_read_fails() -> None:
    """The reader answers until its lines fail."""
    reader = _reader(lines=FakeKnobLines(error=OSError('gone')))
    assert reader.answering
    reader.run(post=lambda event: None)
    assert not reader.answering
