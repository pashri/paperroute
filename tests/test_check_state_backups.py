"""Tests for the state backups check script."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from tests.golden.test_golden_state import HOME, RICH_SAVED

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
    assert pins[0]['missing'] is False
    assert pins[0]['destination']['name'] == 'Westmead Station'
    assert pins[0]['destination']['point'] == [800.0, -1900.0]
    assert [way['ticked'] for way in pins[0]['ways']] == [True, True]
    assert pins[0]['ways'][1]['changes'][1]['allow'] == 4
    assert pins[1]['windows'] == [
        {'days': [0, 1, 2, 3, 4], 'start': '17:00:00', 'end': '18:00:00'},
    ]
    auto = found['auto']
    assert auto['overridden_at'] == '2026-10-16T17:20:00+11:00'
    assert auto['occurrence']['ref'] == pins[1]['ref']
    assert auto['occurrence']['start'] == '2026-10-16T06:00:00+00:00'
    assert (auto['before'], auto['before_pinned']) == (pins[0]['ref'], True)


def test_digest_unpinned_in_full(check_state_backups: ModuleType) -> None:
    """An unpinned place is digested like a pin, not by ref alone."""
    found = check_state_backups.digest(
        saved=replace(RICH_SAVED, unpinned=HOME),
    )
    assert found['unpinned'] == found['pins'][1]


def test_plain_sorts_sets(check_state_backups: ModuleType) -> None:
    """Sets come out in one order whatever order they were built in."""
    assert check_state_backups.plain(frozenset({4, 0, 2})) == [0, 2, 4]
    assert check_state_backups.plain({'b', 'a'}) == ['a', 'b']


def test_empty_dir_fails(
    check_state_backups: ModuleType, tmp_path: Path,
) -> None:
    """A folder with no state files fails the run."""
    assert check_state_backups.main(['--dir', str(tmp_path)]) == 1


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
