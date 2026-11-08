"""Tests for the state backups check script."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

ROOT: Final[Path] = Path(__file__).parents[1]
SCRIPT: Final[Path] = ROOT / 'scripts' / 'check_state_backups.py'
GOLDEN_STATE: Final[Path] = ROOT / 'tests' / 'golden' / 'state.json'


@pytest.fixture(name='check_state_backups', scope='module')
def _check_state_backups() -> ModuleType:
    """
    Import the backups check script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location(
        'check_state_backups', SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_golden_state_checks_clean(
    check_state_backups: ModuleType, tmp_path: Path,
) -> None:
    """The golden state loads, saves stably and digests both pins."""
    found, problems = check_state_backups.check_file(
        path=GOLDEN_STATE, work=tmp_path,
    )
    assert problems == []
    assert found is not None
    pins = found['pins']
    assert len(pins) == 2
    assert (pins[1]['kind'], pins[1]['routes']) == ('bus', ['606', '609'])


def test_bad_file_reported(
    check_state_backups: ModuleType, tmp_path: Path,
) -> None:
    """A file that is not JSON is a problem and fails the run."""
    backups = tmp_path / 'backups'
    backups.mkdir()
    bad = backups / 'state-x.json'
    bad.write_text('{', encoding='utf-8')
    work = tmp_path / 'work'
    work.mkdir()
    found, problems = check_state_backups.check_file(path=bad, work=work)
    assert found is None
    assert problems
    assert check_state_backups.main(['--dir', str(backups)]) == 1
    assert bad.read_text(encoding='utf-8') == '{'
