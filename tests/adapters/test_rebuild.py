"""Tests for the child process that rebuilds the timetable."""

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Final

import pytest

from paperroute_transit.adapters import rebuild as rebuild_module
from paperroute_transit.adapters.rebuild import (
    REBUILD_MIN_AVAILABLE,
    ChildRebuilder,
    NullBuilder,
    idle_io,
    mem_available,
    refresh_command,
    refresh_env,
)

BUILD_SCRIPT: Final[Path] = Path(sys.executable).parent / 'paperroute-build'


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


@pytest.mark.parametrize(
    ('report', 'ok'),
    [
        ({'rebuilt': True, 'all_failed': True}, True),
        ({'rebuilt': False, 'all_failed': True}, False),
    ],
)
async def test_child_rebuilder_counts_a_reported_rebuild(
    report: dict[str, bool], ok: bool,
) -> None:
    """Exit 1 after a timetable was swapped in still counts as built."""
    rebuilder = child(
        code=(
            "print('progress merge')\n"
            "print('{not json')\n"
            f'print({json.dumps(json.dumps(report))})\n'
            'raise SystemExit(1)'
        ),
    )
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
        str(BUILD_SCRIPT),
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


def test_refresh_runs_build_script() -> None:
    """The refresh is the build script beside the running interpreter."""
    assert refresh_command(
        timetable=Path('/d/timetable.db'), error_log=None,
    ) == (
        str(Path(sys.executable).parent / 'paperroute-build'),
        'refresh',
        '--data-dir',
        '/d',
    )


def test_build_script_installed() -> None:
    """The build script is installed beside the interpreter."""
    assert BUILD_SCRIPT.exists()


async def test_child_rebuilder_force_flag(tmp_path: Path) -> None:
    """A forced rebuild runs the command with --force on the end."""
    out = tmp_path / 'argv'
    rebuilder = ChildRebuilder(
        command=(
            sys.executable,
            '-c',
            'import sys, pathlib; '
            'pathlib.Path(sys.argv[1]).write_text(" ".join(sys.argv))',
            str(out),
        ),
        env=dict(os.environ),
        available=lambda: None,
    )
    assert await rebuilder.rebuild(force=True)
    assert out.read_text().split()[-1] == '--force'
    assert await rebuilder.rebuild()
    assert out.read_text().split()[-1] == str(out)


async def test_null_builder_ignores_force() -> None:
    """Without --rebuild a forced rebuild fails too."""
    assert await NullBuilder().rebuild(force=True) is False


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


def test_idle_io_prefixes_ionice_when_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command runs behind ionice's idle class, if ionice exists."""
    monkeypatch.setattr(
        shutil, 'which', lambda name: '/usr/bin/ionice',
    )
    assert idle_io(command=('a', 'b')) == (
        '/usr/bin/ionice', '-c', '3', 'a', 'b',
    )
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert idle_io(command=('a', 'b')) == ('a', 'b')


async def test_rebuild_fails_when_the_child_cannot_start(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing executable fails the rebuild, is logged, leaves no child.

    Without ionice the missing executable itself fails to spawn; behind
    ionice it is ionice that fails, with a non-zero exit instead.
    """
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    rebuilder = ChildRebuilder(
        command=('/nonexistent/paperroute-nothing',),
        env=dict(os.environ),
        available=lambda: None,
    )
    with caplog.at_level(logging.ERROR):
        assert not await rebuilder.rebuild()
    assert rebuilder.process is None
    assert 'timetable rebuild could not start' in caplog.messages
