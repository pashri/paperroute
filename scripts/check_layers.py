"""Check that no package imports one from a higher layer."""

import ast
import re
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

KIT_RANKS: Final[dict[str, int]] = {
    'paperroute_hardware': 0,
    'paperroute_device': 1,
    'paperroute_transit': 2,
}
APP_RANK: Final[int] = 3
TESTKIT: Final[str] = 'paperroute_testkit'
TESTKIT_RANK: Final[int] = 3
TEST_ONLY: Final[frozenset[str]] = frozenset({TESTKIT, 'tests'})
LOOKUPS: Final[frozenset[str]] = frozenset({'files', 'import_module'})
DISTRIBUTION: Final[re.Pattern[str]] = re.compile(r'[A-Za-z0-9._-]+')


@dataclass(frozen=True)
class Tree:
    """
    One importable package and its tests.

    Attributes
    ----------
    name : str
        The import name, such as ``paperroute_device``.
    rank : int | None
        The layer, lowest first; None for an unknown package.
    src : Path | None
        The package directory; None for the repository's own tests.
    tests : Path | None
        The package's tests directory, if it has one.
    """

    name: str
    rank: int | None
    src: Path | None
    tests: Path | None


def package_dirs(*, root: Path) -> list[Path]:
    """
    List the package directories of every workspace package and app.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    list[Path]
        Each ``packages/*/src/<name>`` and ``apps/*/src/<name>``, by
        path.
    """
    return sorted(
        path.parent
        for pattern in (
            'packages/*/src/*/__init__.py',
            'apps/*/src/*/__init__.py',
        )
        for path in root.glob(pattern)
    )


def ranks(*, root: Path) -> dict[str, int]:
    """
    Give the layer of each package in a repository.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    dict[str, int]
        The kits at their ranks, the testkit at ``TESTKIT_RANK`` and
        each ``apps/*/src/<name>`` at ``APP_RANK``.
    """
    return {
        **KIT_RANKS,
        TESTKIT: TESTKIT_RANK,
        **{
            path.parent.name: APP_RANK
            for path in root.glob('apps/*/src/*/__init__.py')
        },
    }


def trees(*, root: Path) -> list[Tree]:
    """
    Find every workspace package and app under a repository.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    list[Tree]
        Each package and app by path, then the repository's own tests.
    """
    known = ranks(root=root)
    return [
        *(
            Tree(
                name=src.name,
                rank=known.get(src.name),
                src=src,
                tests=(
                    src.parents[1] / 'tests'
                    if (src.parents[1] / 'tests').is_dir()
                    else None
                ),
            )
            for src in package_dirs(root=root)
        ),
        Tree(name='tests', rank=APP_RANK, src=None, tests=root / 'tests'),
    ]


def own_package(*, path: Path) -> str:
    """
    Name the top-level package a file belongs to.

    Parameters
    ----------
    path : Path
        A Python file.

    Returns
    -------
    str
        The outermost directory above the file that holds an
        ``__init__.py``.
    """
    package = path.parent
    while (package.parent / '__init__.py').is_file():
        package = package.parent
    return package.name


def module_exists(*, path: Path, dotted: str) -> bool:
    """
    Tell whether a dotted name is a module or package in its tree.

    Parameters
    ----------
    path : Path
        The file holding the name, used to find the repository root.
    dotted : str
        A dotted name such as ``paperroute_sydney.build``.

    Returns
    -------
    bool
        True when the name's package lives in the repository holding
        ``path`` and the rest of the name is a module or package in it.
    """
    root = next(
        (parent for parent in path.parents if (parent / 'apps').is_dir()),
        None,
    )
    if root is None:
        return False
    for src in package_dirs(root=root):
        if src.name == dotted.split('.')[0]:
            target = src.joinpath(*dotted.split('.')[1:])
            return (
                target.with_suffix('.py').is_file()
                or (target / '__init__.py').is_file()
            )
    return False


def docstrings(*, module: ast.Module) -> set[int]:
    """
    Find the string constants that stand alone as statements.

    Parameters
    ----------
    module : ast.Module
        A parsed file.

    Returns
    -------
    set[int]
        The ``id`` of each docstring constant node.
    """
    return {
        id(node.value)
        for node in ast.walk(module)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
    }


def named(*, node: ast.AST, path: Path) -> Iterator[str]:
    """
    Give the dotted names one syntax node imports.

    Parameters
    ----------
    node : ast.AST
        Any node of a parsed file.
    path : Path
        The file, for relative imports.

    Yields
    ------
    str
        Each dotted or top-level name the node imports.
    """
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        yield own_package(path=path) if node.level else node.module or ''
    elif (
        isinstance(node, ast.Call)
        and getattr(node.func, 'id', getattr(node.func, 'attr', '')) in LOOKUPS
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        yield node.args[0].value


def imported(
    *,
    path: Path,
    ranks: dict[str, int],  # pylint: disable=redefined-outer-name
) -> set[str]:
    """
    List the top-level names a file imports.

    Counts imports anywhere in the file, the first argument of
    ``files(...)`` and ``import_module(...)``, and any other string that
    names a module or package of a ranked tree, such as
    ``'paperroute_sydney.build'``. Docstrings and strings that only share a
    prefix, such as ``'paperroute.local'``, do not count.

    Parameters
    ----------
    path : Path
        A Python file.
    ranks : dict[str, int]
        The repository's ranked packages, whose module strings count.

    Returns
    -------
    set[str]
        Top-level import names; a relative import gives the file's own
        package.
    """
    module = ast.parse(path.read_text(encoding='utf-8'))
    skip = docstrings(module=module)
    names = {
        name.split('.')[0]
        for node in ast.walk(module)
        for name in named(node=node, path=path)
    }
    names.update(
        node.value.split('.')[0]
        for node in ast.walk(module)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value.split('.')[0] in ranks
        and '.' in node.value
        and module_exists(path=path, dotted=node.value)
    )
    return names


def allowed(
    *, tree: Tree, name: str, test: bool, known: dict[str, int],
) -> bool:
    """
    Tell whether code in a tree may import a name.

    Parameters
    ----------
    tree : Tree
        The tree holding the importing file.
    name : str
        The top-level name imported.
    test : bool
        Whether the importing file is a test.
    known : dict[str, int]
        The repository's ranked packages.

    Returns
    -------
    bool
        True when the import keeps the layers: its own package or a
        lower rank, so two apps at the same rank never import each
        other. The repository's own tests may import anything.
    """
    if tree.src is None or name == tree.name or (test and name in TEST_ONLY):
        return True
    if name in TEST_ONLY:
        return False
    if name not in known or tree.rank is None:
        return True
    return known[name] < tree.rank


def layer_problems(*, root: Path, tree: Tree) -> Iterator[str]:
    """
    Report a package that has no layer.

    Parameters
    ----------
    root : Path
        The repository root, for relative paths.
    tree : Tree
        The tree to check.

    Yields
    ------
    str
        One line when the tree is unranked.
    """
    if tree.rank is None and tree.src is not None:
        yield (
            f'{tree.src.relative_to(root).as_posix()}: '
            f'{tree.name} has no layer'
        )


def checked_files(*, root: Path, tree: Tree) -> list[tuple[Path, bool]]:
    """
    List the Python files of one tree, each marked as test or source.

    Parameters
    ----------
    root : Path
        The repository root, for the root ``conftest.py``.
    tree : Tree
        The tree to list.

    Returns
    -------
    list[tuple[Path, bool]]
        Each file with True for a test; the repository's own tests also
        hold the root ``conftest.py``.
    """
    found = []
    if tree.src is not None:
        found += [(path, False) for path in sorted(tree.src.rglob('*.py'))]
    else:
        found += [
            (path, True)
            for path in [root / 'conftest.py']
            if path.is_file()
        ]
    if tree.tests is not None and tree.tests.is_dir():
        found += [(path, True) for path in sorted(tree.tests.rglob('*.py'))]
    return found


def import_problems(*, root: Path, tree: Tree) -> Iterator[str]:
    """
    Report the imports in one tree's source and tests that break a rule.

    Parameters
    ----------
    root : Path
        The repository root, for relative paths.
    tree : Tree
        The tree to check.

    Yields
    ------
    str
        One line per bad import.
    """
    known = ranks(root=root)
    for path, test in checked_files(root=root, tree=tree):
        for name in sorted(imported(path=path, ranks=known)):
            if not allowed(tree=tree, name=name, test=test, known=known):
                yield (
                    f'{path.relative_to(root).as_posix()}: '
                    f'{tree.name} may not import {name}'
                )


def dependency_problems(*, root: Path, tree: Tree) -> Iterator[str]:
    """
    Report the workspace distributions at or above a package's rank.

    Parameters
    ----------
    root : Path
        The repository root, for relative paths.
    tree : Tree
        The tree to check.

    Yields
    ------
    str
        One line per bad dependency, naming its import name.
    """
    if tree.src is None or tree.rank is None:
        return
    pyproject = tree.src.parents[1] / 'pyproject.toml'
    if not pyproject.is_file():
        return
    known = ranks(root=root)
    project = tomllib.loads(pyproject.read_text(encoding='utf-8'))
    for requirement in project.get('project', {}).get('dependencies', []):
        match = DISTRIBUTION.match(requirement)
        name = re.sub(r'[-_.]+', '_', match.group().lower()) if match else ''
        if name != tree.name and known.get(name, -1) >= tree.rank:
            yield (
                f'{pyproject.relative_to(root).as_posix()}: '
                f'{tree.name} may not import {name}'
            )


def problems(*, root: Path) -> list[str]:
    """
    Find every import and dependency that points to a higher layer.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    list[str]
        One line per problem, as ``<path>: <importer> may not import
        <imported>``; empty when the layers hold.
    """
    return [
        line
        for tree in trees(root=root)
        for check in (
            layer_problems,
            import_problems,
            dependency_problems,
        )
        for line in check(root=root, tree=tree)
    ]


def main() -> int:
    """
    Print the layer problems in this repository.

    Returns
    -------
    int
        1 when there are problems, else 0.
    """
    found = problems(root=Path(__file__).resolve().parents[1])
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
