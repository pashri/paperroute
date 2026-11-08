"""Tests for what the device's code imports."""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).parents[1]
SRC: Final[tuple[Path, ...]] = (
    ROOT / 'src' / 'paperroute',
    *sorted(
        path.parent
        for path in ROOT.glob('packages/*/src/*/__init__.py')
        if path.parent.name != 'paperroute_testkit'
    ),
)
HARDWARE: Final[Path] = (
    ROOT / 'packages' / 'paperroute-hardware' / 'src' / 'paperroute_hardware'
)
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
KIT_TEXT: Final[frozenset[str]] = frozenset(
    {'.py', '.html', '.css', '.js', '.toml', '.txt'},
)
NSW: Final[re.Pattern[str]] = re.compile(r'(?i)\bnsw')
SCREEN_IMPORTS: Final[str] = (
    'import paperroute.screen, sys; '
    'print(*sorted(sys.modules))'
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


def test_screen_loads_no_board_modules() -> None:
    """The screen command loads only the hardware package and itself."""
    result = subprocess.run(
        [sys.executable, '-c', SCREEN_IMPORTS],
        check=True,
        capture_output=True,
        text=True,
    )
    offenders = [
        name
        for name in result.stdout.split()
        if name.split('.')[0].startswith('paperroute')
        and name not in {'paperroute', 'paperroute.screen'}
        and name.split('.')[0] != 'paperroute_hardware'
    ]
    assert not offenders


def source_root(*, path: Path) -> Path:
    """
    Find the source root holding a file.

    Parameters
    ----------
    path : Path
        A file under one of the source roots.

    Returns
    -------
    Path
        The import package directory, such as ``src/paperroute``.
    """
    return next(root for root in SRC if path.is_relative_to(root))


def sources(*, sub: str = '') -> list[Path]:
    """
    List the Python files of every source root.

    Parameters
    ----------
    sub : str
        A subpackage to look in, such as ``core``; every file when empty.

    Returns
    -------
    list[Path]
        The files, by path.
    """
    return sorted(
        path
        for root in SRC
        for path in (root / sub).glob('*.py' if sub else '**/*.py')
    )


def under(*, path: Path) -> str:
    """
    Give a source file's path inside its package.

    Parameters
    ----------
    path : Path
        A file under one of the source roots.

    Returns
    -------
    str
        The path below the import package, such as
        ``adapters/state_file.py``.
    """
    return path.relative_to(source_root(path=path)).as_posix()


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
    parent = source_root(path=path).parent
    package = list(path.relative_to(parent).with_suffix('').parts)
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
        for path in sources(sub='core')
        for name in imported_names(path=path)
        if name.split('.')[1:2] == ['adapters']
        or name.split('.')[0] in {'pydantic', 'skia', 'numpy'}
    ]
    assert not offenders


def test_pydantic_only_in_state_file() -> None:
    """Only the state file and its helpers import pydantic."""
    allowed = {
        f'adapters/{name}'
        for name in (
            'state_file.py',
            'state_migrations.py',
            'state_auto.py',
            'state_settings.py',
        )
    }
    offenders = [
        str(path)
        for path in sources()
        if under(path=path) not in allowed
        and any(
            name.split('.')[0] == 'pydantic'
            for name in imported_names(path=path)
        )
    ]
    assert not offenders


def test_render_takes_only_faces_and_greys_from_build() -> None:
    """Render reads only face, grey and supersample constants from build."""
    allowed = {'DARK', 'LIGHT', 'SUPERSAMPLE'}
    builds = {('paperroute', 'build'), ('paperroute_transit', 'build')}
    offenders = [
        f'{path.name}: {alias.name}'
        for path in sources(sub='render')
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
        if isinstance(node, ast.ImportFrom)
        and tuple(resolve_from(path=path, node=node).split('.')[:2]) in builds
        for alias in node.names
        if alias.name not in allowed and not alias.name.endswith('_FACE')
    ]
    assert not offenders


def test_gpiod_only_in_the_line_adapters() -> None:
    """Only the Linux panel bus and the knobs' lines import gpiod."""
    allowed = {
        HARDWARE / 'adapters' / 'panel_bus.py',
        HARDWARE / 'adapters' / 'knob_lines.py',
    }
    offenders = [
        str(path)
        for path in sources()
        if path not in allowed
        and any(
            name.split('.')[0] == 'gpiod'
            for name in imported_names(path=path)
        )
    ]
    assert not offenders


def test_shell_screens_use_no_board_drawing() -> None:
    """Shell screens take their drawing and text from the device kit."""
    board = ROOT / 'src' / 'paperroute'
    kit = (
        ROOT / 'packages' / 'paperroute-device' / 'src' / 'paperroute_device'
    )
    drawing = {
        'paperroute.render.stop_view',
        'paperroute.render.map_view',
        'paperroute.render.chrome',
    }
    banned = {
        kit / 'render' / 'panel.py': {*drawing, 'paperroute.core.stop_view'},
        kit / 'render' / 'notice.py': drawing,
        kit / 'render' / 'sleep.py': drawing,
        board / 'core' / 'wifi.py': {'paperroute.core.stop_view'},
        board / 'core' / 'banners.py': {'paperroute.core.stop_view'},
        board / 'core' / 'panel_screens.py': {'paperroute.core.stop_view'},
        kit / 'adapters' / 'device_page.py': {'paperroute.core.stop_view'},
    }
    offenders = {
        path.name: sorted(imported_names(path=path) & modules)
        for path, modules in banned.items()
        if imported_names(path=path) & modules
    }
    assert not offenders


def kit_texts() -> list[tuple[Path, str]]:
    """
    Read the device kit's text files.

    Returns
    -------
    list[tuple[Path, str]]
        Each source, page, style, script, TOML and text file under the
        kit's ``src``, with its text; binary files are skipped.
    """
    kit = ROOT / 'packages' / 'paperroute-device' / 'src'
    return [
        (path, path.read_text(encoding='utf-8'))
        for path in sorted(kit.rglob('*'))
        if path.is_file() and path.suffix in KIT_TEXT
    ]


def transit_texts() -> list[tuple[Path, str]]:
    """
    Read the transit package's text files.

    Returns
    -------
    list[tuple[Path, str]]
        Each source, page, style, script, TOML and text file under the
        transit package's ``src``, with its text; binary files are
        skipped.
    """
    transit = ROOT / 'packages' / 'paperroute-transit' / 'src'
    return [
        (path, path.read_text(encoding='utf-8'))
        for path in sorted(transit.rglob('*'))
        if path.is_file() and path.suffix in KIT_TEXT
    ]


def test_transit_names_no_tfnsw() -> None:
    """No file of the transit package names TfNSW or NSW."""
    offenders = [
        str(path)
        for path, text in transit_texts()
        if 'tfnsw' in text.lower() or NSW.search(text)
    ]
    assert not offenders


def test_transit_names_no_sydney() -> None:
    """No file of the transit package names Sydney."""
    offenders = [
        str(path)
        for path, text in transit_texts()
        if 'sydney' in text.lower()
    ]
    assert not offenders


def test_device_kit_names_no_tfnsw() -> None:
    """No file of the device kit names TfNSW."""
    offenders = [
        str(path) for path, text in kit_texts() if 'tfnsw' in text.lower()
    ]
    assert not offenders


def test_device_kit_names_no_sydney() -> None:
    """No file of the device kit names Sydney or its time zone."""
    offenders = [
        str(path)
        for path, text in kit_texts()
        if 'SYDNEY' in text or 'Sydney' in text
    ]
    assert not offenders


def test_device_pages_import_no_feed_config() -> None:
    """The Device and setup pages take feed settings from the app."""
    kit = (
        ROOT / 'packages' / 'paperroute-device' / 'src' / 'paperroute_device'
    )
    banned = {
        'paperroute.core.feeds',
        'paperroute.core.quota',
        'paperroute.adapters.tfnsw',
    }
    offenders = {
        name: sorted(imported_names(path=kit / name) & banned)
        for name in ('adapters/web_device.py', 'adapters/device_page.py')
        if imported_names(path=kit / name) & banned
    }
    assert not offenders


def test_web_display_imports_no_board_pages() -> None:
    """The web display takes Sydney's pages from the app."""
    banned = {
        'paperroute.planner_port',
        'paperroute.adapters.web_pins',
        'paperroute.adapters.web_ways',
        'paperroute.adapters.web_connection',
        'paperroute.adapters.web_suggest',
    }
    path = ROOT / 'src' / 'paperroute' / 'adapters' / 'web_display.py'
    names = imported_names(path=path)
    assert not names & banned
    assert 'SYDNEY' not in path.read_text(encoding='utf-8')
