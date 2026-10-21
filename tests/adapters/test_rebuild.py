"""Tests for the child process that rebuilds the timetable."""

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from paperroute.adapters import rebuild as rebuild_module
from paperroute.adapters.rebuild import (
    REBUILD_MIN_AVAILABLE,
    ChildRebuilder,
    NullBuilder,
    mem_available,
    refresh_command,
    refresh_env,
)


def child(*, code: str) -> ChildRebuilder:
    """
    Make a rebuilder running a Python snippet, with memory unknown.

    Parameters
    ----------
    code : str
        Python source.

    Returns
    -------
    ChildRebuilder
        The rebuilder.
    """
    return ChildRebuilder(
        command=(sys.executable, '-c', code),
        env=dict(os.environ),
        available=lambda: None,
    )


@pytest.mark.parametrize(('code', 'ok'), [(0, True), (1, False)])
async def test_child_rebuilder_reports_exit(code: int, ok: bool) -> None:
    """Exit 0 is a success; any other exit is a failure."""
    rebuilder = child(code=f'raise SystemExit({code})')
    assert await rebuilder.rebuild() is ok


async def test_child_rebuilder_runs_niced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The child runs at nice 19 and its output is logged."""
    rebuilder = child(code='import os; print(os.nice(0))')
    with caplog.at_level(logging.INFO):
        assert await rebuilder.rebuild()
    assert 'rebuild: 19' in caplog.messages


def gone(*, pid: int) -> bool:
    """
    Say whether a process no longer exists.

    Parameters
    ----------
    pid : int
        Process ID.

    Returns
    -------
    bool
        True when no process has the ID.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def test_stop_terminates_the_rebuild() -> None:
    """Stopping ends a running child within the wait."""
    rebuilder = child(code='import time; time.sleep(60)')
    task = asyncio.create_task(rebuilder.rebuild())
    while rebuilder.process is None:
        await asyncio.sleep(0.01)
    pid = rebuilder.process.pid
    started = time.monotonic()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await rebuilder.stop()
    assert gone(pid=pid)
    assert time.monotonic() - started < 11


async def test_rebuild_skipped_when_memory_low() -> None:
    """Below the memory floor the child never starts and it fails."""
    rebuilder = ChildRebuilder(
        command=(sys.executable, '-c', 'raise SystemExit(0)'),
        env={},
        available=lambda: REBUILD_MIN_AVAILABLE - 1,
    )
    assert not await rebuilder.rebuild()
    assert rebuilder.process is None


def test_mem_available_reads_meminfo(tmp_path: Path) -> None:
    """MemAvailable is read in bytes; a missing file gives None."""
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text(
        'MemTotal: 400000 kB\nMemAvailable: 300000 kB\n', encoding='ascii',
    )
    assert mem_available(meminfo=meminfo) == 300000 * 1024
    assert mem_available(meminfo=tmp_path / 'missing') is None
    meminfo.write_text('MemTotal: 400000 kB\n', encoding='ascii')
    assert mem_available(meminfo=meminfo) is None


async def test_stop_without_a_child_does_nothing() -> None:
    """Stopping when nothing runs leaves no process behind."""
    rebuilder = child(code='raise SystemExit(0)')
    await rebuilder.stop()
    assert rebuilder.process is None


def test_refresh_command_shape() -> None:
    """The refresh runs on the timetable's directory, temp files in work/."""
    timetable = Path('/var/lib/paperroute/timetable.db')
    error_log = Path('/var/lib/paperroute/log/errors.jsonl')
    assert refresh_command(timetable=timetable, error_log=error_log) == (
        sys.executable,
        '-m',
        'paperroute.build',
        'refresh',
        '--data-dir',
        '/var/lib/paperroute',
        '--error-log',
        '/var/lib/paperroute/log/errors.jsonl',
    )
    assert refresh_command(timetable=timetable, error_log=None)[-1] == (
        '/var/lib/paperroute'
    )
    assert refresh_env(timetable=timetable)['TMPDIR'] == (
        '/var/lib/paperroute/work'
    )


async def test_null_builder_refuses_and_says_so_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without --rebuild every rebuild fails, logged the first time only."""
    builder = NullBuilder()
    with caplog.at_level(logging.INFO):
        results = [await builder.rebuild() for _ in range(2)]
        await builder.stop()
    assert results == [False, False]
    assert len(caplog.records) == 1


async def test_stop_kills_a_child_ignoring_sigterm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that ignores SIGTERM is killed after the wait."""
    monkeypatch.setattr(rebuild_module, 'STOP_WAIT', 0.5)
    rebuilder = child(
        code=(
            'import signal, sys, time;'
            ' signal.signal(signal.SIGTERM, signal.SIG_IGN);'
            ' print("ready", flush=True); time.sleep(60)'
        ),
    )
    task = asyncio.create_task(rebuilder.rebuild())
    while rebuilder.process is None:
        await asyncio.sleep(0.01)
    process = rebuilder.process
    assert process.stdout is not None
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await rebuilder.stop()
    assert process.returncode == -signal.SIGKILL
    assert gone(pid=process.pid)


async def test_overlong_output_line_stops_the_child(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A line past the reader's limit fails the rebuild and ends the child."""
    rebuilder = child(
        code=(
            'import sys, time; print("x" * 100000, flush=True);'
            ' time.sleep(60)'
        ),
    )
    with caplog.at_level(logging.ERROR):
        assert not await rebuilder.rebuild()
    assert rebuilder.process is None
    assert 'reading the rebuild output failed' in caplog.messages
