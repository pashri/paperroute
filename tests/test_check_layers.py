"""Tests for the import-boundary check script."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

ROOT: Final[Path] = Path(__file__).parents[1]
SCRIPT: Final[Path] = ROOT / 'scripts' / 'check_layers.py'
HARDWARE: Final[str] = 'packages/paperroute-hardware'
DEVICE: Final[str] = 'packages/paperroute-device'
TESTKIT: Final[str] = 'packages/paperroute-testkit'
CLEAN: Final[dict[str, str]] = {
    'src/paperroute/__init__.py': '',
    'src/paperroute/build/__init__.py': '',
    'src/paperroute/app.py': 'from paperroute_device import y\n',
    'tests/__init__.py': '',
    'tests/test_app.py': 'import paperroute_testkit\nimport paperroute\n',
    f'{HARDWARE}/pyproject.toml': (
        '[project]\nname = "paperroute-hardware"\ndependencies = []\n'
    ),
    f'{HARDWARE}/src/paperroute_hardware/__init__.py': '',
    f'{HARDWARE}/src/paperroute_hardware/x.py': 'import os\n',
    f'{HARDWARE}/tests/__init__.py': '',
    f'{DEVICE}/pyproject.toml': (
        '[project]\nname = "paperroute-device"\n'
        'dependencies = ["paperroute-hardware", "numpy>=2"]\n'
    ),
    f'{DEVICE}/src/paperroute_device/__init__.py': '',
    f'{DEVICE}/src/paperroute_device/y.py': (
        'from . import z\nfrom paperroute_hardware import x\n'
    ),
    f'{DEVICE}/src/paperroute_device/z.py': '',
    f'{TESTKIT}/pyproject.toml': (
        '[project]\nname = "paperroute-testkit"\n'
        'dependencies = ["paperroute"]\n'
    ),
    f'{TESTKIT}/src/paperroute_testkit/__init__.py': 'import paperroute\n',
}


@pytest.fixture(name='check_layers', scope='module')
def _check_layers() -> ModuleType:
    """
    Import the boundary check script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location('check_layers', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build(*, root: Path, extra: dict[str, str]) -> Path:
    """
    Write the clean tree plus some extra files under a directory.

    Parameters
    ----------
    root : Path
        Directory to write into.
    extra : dict[str, str]
        Relative paths and texts that add to or replace the clean tree.

    Returns
    -------
    Path
        The same directory.
    """
    for name, text in (CLEAN | extra).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return root


def test_problems_empty_for_clean_tree(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """A tree whose imports only point down has no problems."""
    root = build(root=tmp_path, extra={})
    assert check_layers.problems(root=root) == []


def test_problems_flags_upward_import(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """A hardware module importing the device kit is one problem."""
    name = f'{HARDWARE}/src/paperroute_hardware/x.py'
    root = build(root=tmp_path, extra={name: 'import paperroute_device.x\n'})
    assert check_layers.problems(root=root) == [
        f'{name}: paperroute_hardware may not import paperroute_device',
    ]


def test_problems_flags_import_inside_function(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """An import inside a function counts like one at the top."""
    name = f'{HARDWARE}/src/paperroute_hardware/x.py'
    text = 'def go() -> None:\n    from paperroute_device import y\n'
    root = build(root=tmp_path, extra={name: text})
    assert check_layers.problems(root=root) == [
        f'{name}: paperroute_hardware may not import paperroute_device',
    ]


def test_problems_tells_paperroute_from_paperroute_device(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """``paperroute_device`` is not the app just because of its prefix."""
    name = f'{DEVICE}/src/paperroute_device/z.py'
    root = build(root=tmp_path, extra={name: 'import paperroute_device.y\n'})
    assert check_layers.problems(root=root) == []


def test_problems_flags_testkit_in_source(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """Source code may not import the testkit."""
    name = f'{DEVICE}/src/paperroute_device/z.py'
    root = build(root=tmp_path, extra={name: 'import paperroute_testkit\n'})
    assert check_layers.problems(root=root) == [
        f'{name}: paperroute_device may not import paperroute_testkit',
    ]


def test_problems_allows_testkit_in_tests(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """Package tests may import the testkit, ``tests`` and lower ranks."""
    name = f'{DEVICE}/tests/test_y.py'
    text = (
        'import paperroute_testkit\nfrom tests import helpers\n'
        'from paperroute_hardware import x\nfrom paperroute_device import y\n'
    )
    root = build(root=tmp_path, extra={name: text})
    assert check_layers.problems(root=root) == []


def test_problems_flags_upward_resource_lookup(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """Reading the app's package data from the device kit is a problem."""
    name = f'{DEVICE}/src/paperroute_device/z.py'
    text = (
        'from importlib.resources import files\n'
        "FONTS = files('paperroute') / 'fonts'\n"
    )
    root = build(root=tmp_path, extra={name: text})
    assert check_layers.problems(root=root) == [
        f'{name}: paperroute_device may not import paperroute',
    ]


def test_problems_flags_upward_module_string(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """A string naming an app module is a problem; a hostname is not."""
    name = f'{DEVICE}/src/paperroute_device/z.py'
    text = (
        '"""Runs paperroute.build for the board."""\n'
        "COMMAND = ('-m', 'paperroute.build')\n"
        "HOST = 'paperroute.local'\n"
        'def go(**kwargs: str) -> None:\n'
        "    go(consumer='paperroute')\n"
    )
    root = build(root=tmp_path, extra={name: text})
    assert check_layers.problems(root=root) == [
        f'{name}: paperroute_device may not import paperroute',
    ]


def test_problems_flags_upward_package_dependency(
    check_layers: ModuleType, tmp_path: Path,
) -> None:
    """A hardware distribution may not depend on the device kit."""
    name = f'{HARDWARE}/pyproject.toml'
    text = (
        '[project]\nname = "paperroute-hardware"\n'
        'dependencies = ["paperroute-device>=0.1"]\n'
    )
    root = build(root=tmp_path, extra={name: text})
    assert check_layers.problems(root=root) == [
        f'{name}: paperroute_hardware may not import paperroute_device',
    ]


def test_problems_empty_for_this_repo(check_layers: ModuleType) -> None:
    """This repository keeps its layers."""
    assert check_layers.problems(root=ROOT) == []
