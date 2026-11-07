"""Tests for how the app finds its package data."""

import ast
from pathlib import Path
from typing import Final

from paperroute_testkit.paths import ROOT

ASSET_ROOT: Final[Path] = ROOT / 'src' / 'paperroute' / 'asset_root.py'
ENTRY_POINTS: Final[tuple[Path, ...]] = (
    ROOT / 'src' / 'paperroute' / '__main__.py',
    ROOT / 'src' / 'paperroute' / 'build' / '__main__.py',
)


def test_only_asset_root_reads_package_data() -> None:
    """No source file but the asset root names the app's package data."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted(
            [*ROOT.glob('src/**/*.py'), *ROOT.glob('packages/*/src/**/*.py')],
        )
        if path != ASSET_ROOT
        and "files('paperroute')" in path.read_text(encoding='utf-8')
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
    """The app and the build command set the asset root first."""
    assert [first_call(path=path) for path in ENTRY_POINTS] == [
        'use_app_assets',
        'use_app_assets',
    ]
