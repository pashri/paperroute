"""Tests for how the app finds its package data."""

import ast
import re
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_device.assets import assets
from paperroute_device_testkit.paths import ROOT
from paperroute_sydney.asset_root import APP_ASSETS, use_app_assets
from paperroute_transit.adapters.board_web import BOARD_WEB, board_path

ASSET_ROOT: Final[Path] = (
    ROOT
    / 'apps'
    / 'paperroute-sydney'
    / 'src'
    / 'paperroute_sydney'
    / 'asset_root.py'
)
ENTRY_POINTS: Final[tuple[Path, ...]] = (
    ROOT
    / 'packages'
    / 'paperroute-transit'
    / 'src'
    / 'paperroute_transit'
    / 'board.py',
)
APP_FILES: Final[re.Pattern[str]] = re.compile(
    r'''files\(\s*(['"])paperroute_sydney(\.[\w.]*)?\1''',
)
READERS: Final[dict[str, Traversable]] = {
    'asset': APP_ASSETS.web,
    'asset_bytes': APP_ASSETS.web,
    'board_text': BOARD_WEB,
    'board_bytes': BOARD_WEB,
    'shell_text': SHELL_WEB,
    'shell_bytes': SHELL_WEB,
}


def test_use_app_assets_sets_app_root() -> None:
    """Pointing the device kit at the app gives the app's asset root."""
    use_app_assets()
    assert assets() == APP_ASSETS


def test_app_web_holds_only_palette() -> None:
    """The app's web directory keeps only its own palette."""
    assert sorted(p.name for p in Path(str(APP_ASSETS.web)).iterdir()) == [
        'palette.css',
    ]


def test_only_asset_root_reads_package_data() -> None:
    """No source file but the asset root names the app's package data."""
    paths = source_files()
    offenders = [
        str(path.relative_to(ROOT))
        for path in paths
        if path != ASSET_ROOT
        and APP_FILES.search(path.read_text(encoding='utf-8'))
    ]
    assert ASSET_ROOT in paths
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
        Every ``.py`` file under ``apps/*/src`` and ``packages/*/src``.
    """
    return sorted(
        [
            *ROOT.glob('apps/*/src/**/*.py'),
            *ROOT.glob('packages/*/src/**/*.py'),
        ],
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


def findable(*, called: str, name: str) -> bool:
    """
    Tell whether a web file is where its reader looks.

    Parameters
    ----------
    called : str
        The reader's name, a key of `READERS`.
    name : str
        The file's ``/``-separated path within a web directory.

    Returns
    -------
    bool
        True when the reader finds the file; the board readers look in
        the app's web directory, then transit's, as `board_path` does.
    """
    if called.startswith('board_'):
        try:
            board_path(name=name)
        except FileNotFoundError:
            return False
        else:
            return True
    return READERS[called].joinpath(*name.split('/')).is_file()


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
        if not findable(called=called, name=name)
    ]
    assert calls
    assert not missing


def test_no_web_file_in_both_places() -> None:
    """
    No web file is in more than one of the shell, transit and the app.

    This records Sydney's choice under Ruling 3: its web directory
    overrides no transit board file. Another city may override one.
    """
    shell = relative_files(root=SHELL_WEB)
    board = relative_files(root=BOARD_WEB)
    app = relative_files(root=APP_ASSETS.web)
    assert shell
    assert board
    assert not shell & app
    assert not board & app
    assert not board & shell
