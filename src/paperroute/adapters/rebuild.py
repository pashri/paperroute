"""Run the timetable refresh as a low-priority child process."""

import asyncio
import logging
import os
import shutil
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
BUILD_SCRIPT: Final[str] = 'paperroute-build'


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
    first_to_kill()


def first_to_kill() -> None:
    """Raise this process's OOM score so the kernel kills it first."""
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


def idle_io(*, command: Sequence[str]) -> tuple[str, ...]:
    """
    Prefix a command so it runs in the idle I/O class, where possible.

    Parameters
    ----------
    command : Sequence[str]
        The command.

    Returns
    -------
    tuple[str, ...]
        The command behind ``ionice -c 3``; unchanged when ``ionice``
        isn't installed.
    """
    ionice = shutil.which('ionice')
    if ionice is None:
        return tuple(command)
    return (ionice, '-c', '3', *command)


def refresh_command(
    *, timetable: Path, error_log: Path | None
) -> tuple[str, ...]:
    """
    Give the command that refreshes a timetable file.

    It runs the build script installed beside the running interpreter.

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
        str(Path(sys.executable).parent / BUILD_SCRIPT),
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

    async def rebuild(self, *, force: bool = False) -> bool:
        """
        Refuse at once, saying so the first time.

        Parameters
        ----------
        force : bool
            Ignored.

        Returns
        -------
        bool
            Always False.
        """
        if not self.logged:
            logger.info(
                'timetable rebuild asked for; not enabled',
                extra={'force': force},
            )
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

    async def rebuild(self, *, force: bool = False) -> bool:
        """
        Run the command and wait for it, unless memory is short.

        Parameters
        ----------
        force : bool
            Add `--force` to the command.

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
        logger.info('timetable rebuild started', extra={'force': force})
        command = (*self.command, '--force') if force else self.command
        self.process = await self.started(command=command)
        if self.process is None:
            return False
        code = await self.finished(process=self.process)
        self.process = None
        logger.info('timetable rebuild finished', extra={'code': code})
        return code == 0

    async def started(
        self, *, command: Sequence[str],
    ) -> asyncio.subprocess.Process | None:
        """
        Start the child at idle priority.

        Parameters
        ----------
        command : Sequence[str]
            The command.

        Returns
        -------
        asyncio.subprocess.Process | None
            The child; None when it could not start.
        """
        try:
            return await asyncio.create_subprocess_exec(
                *idle_io(command=command),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                env=self.env,
                preexec_fn=lower_priority,
            )
        except OSError:
            logger.exception('timetable rebuild could not start')
            return None

    async def finished(self, *, process: asyncio.subprocess.Process) -> int:
        """
        Relay a started child's output until it exits.

        Parameters
        ----------
        process : asyncio.subprocess.Process
            The child.

        Returns
        -------
        int
            Its exit code; -1 when its output could not be read, after
            stopping it.
        """
        try:
            return await self.relayed(process=process)
        except ValueError:
            logger.exception('reading the rebuild output failed')
            await self.stop()
            return -1

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
