"""Tests for shutting down and restarting through systemctl."""

import logging
import sys
from pathlib import Path

import pytest

from paperroute.adapters.power import NullPower, SystemctlPower
from paperroute.core.messages import PowerAction


def stand_in(*, folder: Path, code: int) -> tuple[Path, Path]:
    """
    Write a stand-in systemctl that records its arguments.

    Parameters
    ----------
    folder : Path
        Where to write it.
    code : int
        The exit code it gives.

    Returns
    -------
    tuple[Path, Path]
        The program, and the file it writes its arguments to.
    """
    record = folder / 'argv'
    program = folder / 'systemctl'
    program.write_text(
        f'#!{sys.executable}\n'
        'import sys\n'
        f'open({str(record)!r}, "w").write(" ".join(sys.argv[1:]))\n'
        'print("refused", file=sys.stderr)\n'
        f'raise SystemExit({code})\n',
        encoding='utf-8',
    )
    program.chmod(0o755)
    return program, record


async def test_systemctl_power_runs_command(tmp_path: Path) -> None:
    """The action's verb is passed to systemctl; exit 0 is accepted."""
    program, record = stand_in(folder=tmp_path, code=0)
    power = SystemctlPower(systemctl=str(program))
    assert await power.control(action=PowerAction.OFF)
    assert record.read_text(encoding='utf-8') == 'poweroff'


async def test_systemctl_power_failure_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-zero exit is refused, with systemctl's output at ERROR."""
    program, record = stand_in(folder=tmp_path, code=1)
    power = SystemctlPower(systemctl=str(program))
    assert not await power.control(action=PowerAction.RESTART)
    assert record.read_text(encoding='utf-8') == 'reboot'
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert 'refused' in errors[0].getMessage()


async def test_systemctl_power_missing_program(tmp_path: Path) -> None:
    """A systemctl that isn't there refuses."""
    power = SystemctlPower(systemctl=str(tmp_path / 'missing'))
    assert not await power.control(action=PowerAction.OFF)


async def test_null_power_refuses(caplog: pytest.LogCaptureFixture) -> None:
    """NullPower always refuses, logging the first time only."""
    power = NullPower()
    results = [
        await power.control(action=PowerAction.OFF) for _ in range(2)
    ]
    assert results == [False, False]
    assert len(caplog.records) == 1
