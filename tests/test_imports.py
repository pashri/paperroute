"""Tests for what the device's code imports."""

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

SRC: Final[Path] = Path(__file__).parents[1] / 'src' / 'paperroute'
DEVICE_IMPORTS: Final[str] = (
    'import paperroute.__main__, paperroute.app, sys; '
    "assert not {'osmium', 'shapely'} & set(sys.modules)"
)
REFRESH_RUN: Final[str] = (
    'import sys\n'
    'from paperroute.build.__main__ import main\n'
    "sys.argv = ['build', 'refresh', '--data-dir', sys.argv[1]]\n"
    'try:\n'
    '    main()\n'
    'except SystemExit as stop:\n'
    '    assert stop.code == 1, stop.code\n'
    "assert not {'osmium', 'shapely'} & set(sys.modules)\n"
    "assert 'pydantic' not in sys.modules\n"
)


def test_device_never_imports_build_libraries() -> None:
    """The app and its CLI load without osmium or shapely."""
    result = subprocess.run(
        [sys.executable, '-c', DEVICE_IMPORTS],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_refresh_never_imports_build_libraries(tmp_path: Path) -> None:
    """The nightly refresh loads and runs without osmium or shapely."""
    result = subprocess.run(
        [sys.executable, '-c', REFRESH_RUN, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            'PAPERROUTE_API_KEY_FILE': str(tmp_path / 'no-such-key'),
        },
    )
    assert result.returncode == 0, result.stderr


def resolve_from(*, path: Path, node: ast.ImportFrom) -> str:
    """
    Give the absolute module name of a from-import.

    Parameters
    ----------
    path : Path
        The source file holding the import.
    node : ast.ImportFrom
        The import statement.

    Returns
    -------
    str
        The module, with any leading dots resolved against the file.
    """
    module = node.module or ''
    if not node.level:
        return module
    package = list(path.relative_to(SRC.parent).with_suffix('').parts)
    base = package[: len(package) - node.level]
    return '.'.join([*base, module] if module else base)


def imported_names(*, path: Path) -> set[str]:
    """
    List the modules a source file imports.

    Parameters
    ----------
    path : Path
        The source file.

    Returns
    -------
    set[str]
        Dotted module names from import statements.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(resolve_from(path=path, node=node))
    return names


def test_core_imports_only_core() -> None:
    """No core module imports adapters, pydantic, skia or numpy."""
    offenders = [
        f'{path.name}: {name}'
        for path in sorted((SRC / 'core').glob('*.py'))
        for name in imported_names(path=path)
        if name.startswith('paperroute.adapters')
        or name.split('.')[0] in {'pydantic', 'skia', 'numpy'}
    ]
    assert not offenders


def test_pydantic_only_in_state_file() -> None:
    """Only the state file and its helpers import pydantic."""
    allowed = {
        SRC / 'adapters' / name
        for name in (
            'state_file.py',
            'state_migrations.py',
            'state_auto.py',
            'state_settings.py',
        )
    }
    offenders = [
        str(path)
        for path in sorted(SRC.rglob('*.py'))
        if path not in allowed
        and any(
            name.split('.')[0] == 'pydantic'
            for name in imported_names(path=path)
        )
    ]
    assert not offenders
