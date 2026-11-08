"""Tests for how the app finds its package data."""

import ast
import re
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from paperroute.asset_root import APP_ASSETS, use_app_assets
from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_device.assets import assets
from paperroute_testkit.paths import ROOT

ASSET_ROOT: Final[Path] = ROOT / 'src' / 'paperroute' / 'asset_root.py'
ENTRY_POINTS: Final[tuple[Path, ...]] = (
    ROOT
    / 'packages'
    / 'paperroute-transit'
    / 'src'
    / 'paperroute_transit'
    / 'board.py',
)
APP_FILES: Final[re.Pattern[str]] = re.compile(
    r'''files\(\s*(['"])paperroute(\.[\w.]*)?\1''',
)
READERS: Final[dict[str, Traversable]] = {
    'asset': APP_ASSETS.web,
    'asset_bytes': APP_ASSETS.web,
    'shell_text': SHELL_WEB,
    'shell_bytes': SHELL_WEB,
}


def test_use_app_assets_sets_app_root() -> None:
    """Pointing the device kit at the app gives the app's asset root."""
    use_app_assets()
    assert assets() == APP_ASSETS


def test_only_asset_root_reads_package_data() -> None:
    """No source file but the asset root names the app's package data."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted(
            [*ROOT.glob('src/**/*.py'), *ROOT.glob('packages/*/src/**/*.py')],
        )
        if path != ASSET_ROOT
        and APP_FILES.search(path.read_text(encoding='utf-8'))
    ]
    assert not offenders


def first_call(*, path: Path) -> str | None:
    """
    Name the function the first statement of a file's ``main`` calls.

    Parameters
    ----------
    path : Path
        The source file.

    Returns
    -------
    str | None
        The called name, or None when ``main`` does not start with a
        plain call.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'main'
    )
    body = [
        statement
        for statement in main.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
        )
    ]
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and isinstance(first.value.func, ast.Name)
    ):
        return first.value.func.id
    return None


def test_entry_points_set_assets() -> None:
    """The board sets the asset root first."""
    assert all(path.is_file() for path in ENTRY_POINTS)
    assert [first_call(path=path) for path in ENTRY_POINTS] == ['use_assets']


def source_files() -> list[Path]:
    """
    List the app's and the packages' source files.

    Returns
    -------
    list[Path]
        Every ``.py`` file under ``src`` and ``packages/*/src``.
    """
    return sorted(
        [*ROOT.glob('src/**/*.py'), *ROOT.glob('packages/*/src/**/*.py')],
    )


def named_files(*, path: Path) -> list[tuple[str, str]]:
    """
    Find the web files a source file reads by a constant name.

    Parameters
    ----------
    path : Path
        The source file.

    Returns
    -------
    list[tuple[str, str]]
        The reader called and the ``name=`` it passed, for each call to
        a web file reader whose name is a string constant.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        found += [
            (called, keyword.value.value)
            for keyword in node.keywords
            if called in READERS
            and keyword.arg == 'name'
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ]
    return found


def relative_files(*, root: Traversable, prefix: str = '') -> set[str]:
    """
    List the files under a directory by their relative paths.

    Parameters
    ----------
    root : Traversable
        The directory.
    prefix : str
        The path of ``root`` within the walk, ending in ``/`` if set.

    Returns
    -------
    set[str]
        Each file's ``/``-separated path relative to the walk's start.
    """
    found: set[str] = set()
    for item in root.iterdir():
        if item.is_dir():
            found |= relative_files(root=item, prefix=f'{prefix}{item.name}/')
        elif item.is_file():
            found.add(f'{prefix}{item.name}')
    return found


def test_named_web_files_exist() -> None:
    """Every web file named in the source is where its reader looks."""
    calls = [
        (called, name)
        for path in source_files()
        for called, name in named_files(path=path)
    ]
    missing = [
        (called, name)
        for called, name in calls
        if not READERS[called].joinpath(*name.split('/')).is_file()
    ]
    assert calls
    assert not missing


def test_no_web_file_in_both_places() -> None:
    """No web file is both in the shell and in the app."""
    shell = relative_files(root=SHELL_WEB)
    assert shell
    assert not shell & relative_files(root=APP_ASSETS.web)
