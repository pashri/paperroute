"""Run the timetable refresh as a low-priority child process."""

import asyncio
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

REBUILD_MIN_AVAILABLE: Final[int] = 200 * 1024 * 1024  # bytes
STOP_WAIT: Final[float] = 10.0  # seconds
CHILD_NICE: Final[int] = 19
CHILD_OOM_SCORE: Final[bytes] = b'1000'
MEMINFO: Final[Path] = Path('/proc/meminfo')
OOM_SCORE_ADJ: Final[str] = '/proc/self/oom_score_adj'
KIB: Final[int] = 1024


def mem_available(*, meminfo: Path = MEMINFO) -> int | None:
    """
    Read how much memory the kernel can give without swapping.

    Parameters
    ----------
    meminfo : Path
        The kernel's memory report.

    Returns
    -------
    int | None
        Available bytes; None when the report can't be read (not Linux).
    """
    try:
        lines = meminfo.read_text(encoding='ascii').splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * KIB
    return None


def lower_priority() -> None:
    """
    Run in the child before exec: lowest CPU priority, first to kill.

    It runs between fork and exec, so it makes only bare system calls.
    """
    os.nice(CHILD_NICE)
    try:
        score = os.open(OOM_SCORE_ADJ, os.O_WRONLY)
    except OSError:
        return
    try:
        os.write(score, CHILD_OOM_SCORE)
    except OSError:
        pass
    finally:
        os.close(score)


def refresh_command(
    *, timetable: Path, error_log: Path | None
) -> tuple[str, ...]:
    """
    Give the command that refreshes a timetable file.

    Parameters
    ----------
    timetable : Path
        The timetable file.
    error_log : Path | None
        Where the refresh logs errors, if anywhere.

    Returns
    -------
    tuple[str, ...]
        The command line.
    """
    command = (
        sys.executable,
        '-m',
        'paperroute.build',
        'refresh',
        '--data-dir',
        str(timetable.parent),
    )
    if error_log is None:
        return command
    return (*command, '--error-log', str(error_log))


def refresh_env(*, timetable: Path) -> dict[str, str]:
    """
    Give the refresh's environment: the app's, temp files under work/.

    Parameters
    ----------
    timetable : Path
        The timetable file.

    Returns
    -------
    dict[str, str]
        The environment.
    """
    return {**os.environ, 'TMPDIR': str(timetable.parent / 'work')}


class NullBuilder:
    """A builder that never rebuilds."""

    def __init__(self) -> None:
        self.logged = False

    async def rebuild(self) -> bool:
        """
        Refuse at once, saying so the first time.

        Returns
        -------
        bool
            Always False.
        """
        if not self.logged:
            logger.info('timetable outdated; rebuild not enabled')
            self.logged = True
        return False

    async def stop(self) -> None:
        """Do nothing: nothing runs."""


class ChildRebuilder:
    """
    Rebuild the timetable by running a command as a child process.

    The child runs at the lowest CPU priority and is the kernel's first
    choice to kill when memory runs out.

    Parameters
    ----------
    command : Sequence[str]
        The command line.
    env : Mapping[str, str]
        The child's environment.
    available : Callable[[], int | None]
        Reads the memory available, None when unknown.
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        env: Mapping[str, str],
        available: Callable[[], int | None] = mem_available,
    ) -> None:
        self.command = tuple(command)
        self.env = dict(env)
        self.available = available
        self.process: asyncio.subprocess.Process | None = None

    async def rebuild(self) -> bool:
        """
        Run the command and wait for it, unless memory is short.

        Returns
        -------
        bool
            True when the command exited 0; False when it failed or
            didn't start for lack of memory.
        """
        available = self.available()
        if available is not None and available < REBUILD_MIN_AVAILABLE:
            logger.warning('timetable rebuild skipped: memory low')
            return False
        logger.info('timetable rebuild started')
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            env=self.env,
            preexec_fn=lower_priority,
        )
        try:
            code = await self.relayed(process=self.process)
        except ValueError:
            logger.exception('reading the rebuild output failed')
            await self.stop()
            code = -1
        self.process = None
        logger.info('timetable rebuild finished', extra={'code': code})
        return code == 0

    @staticmethod
    async def relayed(*, process: asyncio.subprocess.Process) -> int:
        """
        Log a child's output lines until it exits.

        Parameters
        ----------
        process : asyncio.subprocess.Process
            The child.

        Returns
        -------
        int
            Its exit code.
        """
        if process.stdout is not None:
            async for line in process.stdout:
                text = line.decode(errors='replace').rstrip()
                logger.info('rebuild: %s', text)
        return await process.wait()

    async def stop(self) -> None:
        """End a running child: SIGTERM, then SIGKILL after a wait."""
        process = self.process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_WAIT)
        except TimeoutError:
            process.kill()
            await process.wait()
