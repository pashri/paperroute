"""Tests for what the device's code imports."""

import ast
import os
import re
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Final

import pytest

ROOT: Final[Path] = Path(__file__).parents[1]
SRC: Final[tuple[Path, ...]] = tuple(
    sorted(
        path.parent
        for path in [
            *ROOT.glob('apps/*/src/*/__init__.py'),
            *ROOT.glob('packages/*/src/*/__init__.py'),
        ]
        if path.parent.name != 'paperroute_testkit'
    ),
)
APPS: Final[frozenset[str]] = frozenset(
    {path.parent.name for path in ROOT.glob('apps/*/src/*/__init__.py')},
)
HARDWARE: Final[Path] = (
    ROOT / 'packages' / 'paperroute-hardware' / 'src' / 'paperroute_hardware'
)
DEVICE_IMPORTS: Final[str] = (
    'import paperroute_sydney.__main__, paperroute_transit.app, sys; '
    "assert not {'osmium', 'shapely'} & set(sys.modules)"
)
REFRESH_RUN: Final[str] = (
    'import sys\n'
    'from pathlib import Path\n'
    'from paperroute_sydney.build.__main__ import main\n'
    'data = Path(sys.argv[1])\n'
    "sys.argv = ['build', 'refresh', '--data-dir', str(data)]\n"
    'try:\n'
    '    main()\n'
    'except SystemExit as stop:\n'
    '    assert stop.code == 1, stop.code\n'
    "assert not {'osmium', 'shapely'} & set(sys.modules)\n"
    "assert 'pydantic' not in sys.modules\n"
    "assert not (data / 'timetable.db').exists()\n"
    "assert not any((data / 'sources').glob('*'))\n"
)
KIT_TEXT: Final[frozenset[str]] = frozenset(
    {'.py', '.html', '.css', '.js', '.toml', '.txt'},
)
STOP_VIEW: Final[str] = 'paperroute_transit.core.stop_view'
NSW: Final[re.Pattern[str]] = re.compile(r'(?i)\bnsw')
KEY_SCHEME: Final[re.Pattern[str]] = re.compile(r'(?i)\bapikey\b|BASE_URL')
APP: Final[Path] = (
    ROOT / 'apps' / 'paperroute-sydney' / 'src' / 'paperroute_sydney'
)
TRANSIT: Final[Path] = (
    ROOT / 'packages' / 'paperroute-transit' / 'src' / 'paperroute_transit'
)
TESTKIT: Final[Path] = (
    ROOT / 'packages' / 'paperroute-testkit' / 'src' / 'paperroute_testkit'
)
CITY_DEFAULTS: Final[dict[str, str | None]] = {
    'Settings': None,
    'BoardSettings': 'peak_windows',
    'Saved': 'settings',
}
SYDNEY_MODULES: Final[frozenset[str]] = frozenset({
    'paperroute_sydney.city',
    'paperroute_sydney.sydney_feeds',
    'paperroute_sydney.__main__',
    'paperroute_sydney.adapters.tfnsw',
    'paperroute_sydney.adapters.trip_planner',
    'paperroute_sydney.adapters.web_suggest',
    'paperroute_sydney.adapters.web_board',
    'paperroute_sydney.core.trip_search',
    'paperroute_sydney.planner_port',
    'paperroute_sydney.build.__main__',
    'paperroute_sydney.build.tfnsw_sources',
    'paperroute_sydney.build.dedupe',
    'paperroute_sydney.build.trainlink',
})
CITY_IMPORTS: Final[str] = (
    'import paperroute_sydney.city, paperroute_transit.build.extras, sys; '
    "loaded = sorted(name for name in sys.modules if name.startswith('google')); "
    'assert not loaded, loaded'
)
MOVED: Final[tuple[str, ...]] = (
    'adapters.board_head',
    'adapters.board_pages',
    'adapters.change_rows',
    'adapters.clocks',
    'adapters.connection_editor',
    'adapters.device_probe',
    'adapters.feed_files',
    'adapters.feed_guard',
    'adapters.gtfs_alerts',
    'adapters.gtfs_rt',
    'adapters.mode_css',
    'adapters.network_db',
    'adapters.network_lines',
    'adapters.pin_editor',
    'adapters.pin_options',
    'adapters.pin_windows',
    'adapters.rebuild',
    'adapters.route_chips',
    'adapters.settings_form',
    'adapters.state_auto',
    'adapters.state_change',
    'adapters.state_file',
    'adapters.state_migrations',
    'adapters.state_settings',
    'adapters.timetable_db',
    'adapters.tk_monitor',
    'adapters.ways_editor',
    'adapters.web_connection',
    'adapters.web_display',
    'adapters.web_pin_edit',
    'adapters.web_pin_list',
    'adapters.web_pin_search',
    'adapters.web_pins',
    'adapters.web_ways',
    'app',
    'app_connections',
    'app_device',
    'app_effects',
    'app_outputs',
    'app_ports',
    'app_render',
    'app_work',
    'board',
    'build.cli',
    'build.extras',
    'build.handoff',
    'build.labels',
    'build.map_style',
    'build.refresh',
    'build.source_state',
    'build.sources',
    'build.swap',
    'build.tile_draw',
    'build.tiles',
    'build.timetable',
    'core.alert_match',
    'core.alert_state',
    'core.alerts_view',
    'core.auto_pins',
    'core.banners',
    'core.chains',
    'core.chooser',
    'core.chooser_station',
    'core.chooser_steps',
    'core.chooser_view',
    'core.connection',
    'core.connection_map',
    'core.connection_model',
    'core.connection_screens',
    'core.connection_strip',
    'core.connection_view',
    'core.cycle',
    'core.device',
    'core.knobs',
    'core.live',
    'core.map_fit',
    'core.map_requests',
    'core.map_state',
    'core.map_view',
    'core.menu',
    'core.messages',
    'core.panel_screens',
    'core.panel_views',
    'core.pin_choices',
    'core.pin_edits',
    'core.places',
    'core.polling',
    'core.quiet',
    'core.quota',
    'core.reach_exits',
    'core.rebuild',
    'core.refresh',
    'core.rhythm',
    'core.rides',
    'core.saved',
    'core.sleep',
    'core.state',
    'core.state_data',
    'core.state_model',
    'core.state_saves',
    'core.state_views',
    'core.stop_view',
    'core.strip_badges',
    'core.strip_extents',
    'core.strip_follow',
    'core.strip_model',
    'core.strip_notes',
    'core.strip_opening',
    'core.strip_select',
    'core.strip_state',
    'core.strip_times',
    'core.strip_vehicles',
    'core.strip_view',
    'core.wifi',
    'logs',
    'measures',
    'observers',
    'ports',
    'render.alerts_view',
    'render.chooser',
    'render.chrome',
    'render.connection_strip',
    'render.connection_strip_map',
    'render.connection_view',
    'render.keep_out',
    'render.map_base',
    'render.map_corners',
    'render.map_labels',
    'render.map_marks',
    'render.map_panes',
    'render.map_view',
    'render.markers',
    'render.others',
    'render.pair_list',
    'render.stop_view',
    'render.strip',
    'render.strip_view',
    'render.trip_marks',
    'summary',
    'web_observer',
)
SCREEN_IMPORTS: Final[str] = (
    'import paperroute_device.screen, sys; '
    'print(*sorted(sys.modules))'
)


def absent(*, name: str) -> bool:
    """
    Tell whether a module cannot be found.

    Parameters
    ----------
    name : str
        A dotted module name.

    Returns
    -------
    bool
        True when no spec is found, or when a parent package is gone.
    """
    try:
        return find_spec(name) is None
    except ModuleNotFoundError:
        return True


@pytest.mark.parametrize('module', MOVED)
def test_engine_lives_in_transit(module: str) -> None:
    """Each moved engine module is in transit and gone from the app."""
    assert find_spec(f'paperroute_transit.{module}') is not None
    assert absent(name=f'paperroute_sydney.{module}')


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


def test_city_loads_no_feed_parsers() -> None:
    """The city and the extras program load without protobuf."""
    result = subprocess.run(
        [sys.executable, '-c', CITY_IMPORTS],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_screen_loads_no_board_modules() -> None:
    """The screen command loads only the hardware kit and itself."""
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
        and name not in {'paperroute_device', 'paperroute_device.screen'}
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
        The import package directory, such as
        ``apps/paperroute-sydney/src/paperroute_sydney``.
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
        Dotted module names from import statements; a from-import also
        gives each name it imports under its module, so ``from
        paperroute_sydney import city`` gives ``paperroute_sydney.city``.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = resolve_from(path=path, node=node)
            names.add(module)
            names.update(f'{module}.{alias.name}' for alias in node.names)
    return names


def test_core_imports_only_core() -> None:
    """No core module imports adapters, pydantic, skia or numpy."""
    paths = sources(sub='core')
    offenders = [
        f'{path.name}: {name}'
        for path in paths
        for name in imported_names(path=path)
        if name.split('.')[1:2] == ['adapters']
        or name.split('.')[0] in {'pydantic', 'skia', 'numpy'}
    ]
    assert TRANSIT / 'core' / 'saved.py' in paths
    assert not offenders


def test_pydantic_only_in_state_file() -> None:
    """Only the state file and its helpers import pydantic."""
    allowed = {
        TRANSIT / 'adapters' / name
        for name in (
            'state_file.py',
            'state_migrations.py',
            'state_auto.py',
            'state_settings.py',
        )
    }
    paths = sources()
    offenders = [
        str(path)
        for path in paths
        if path not in allowed
        and any(
            name.split('.')[0] == 'pydantic'
            for name in imported_names(path=path)
        )
    ]
    assert allowed <= set(paths)
    assert not offenders


def test_render_takes_only_faces_and_greys_from_build() -> None:
    """Render reads only face, grey and supersample constants from build."""
    allowed = {'DARK', 'LIGHT', 'SUPERSAMPLE'}
    builds = {('paperroute_sydney', 'build'), ('paperroute_transit', 'build')}
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
    kit = (
        ROOT / 'packages' / 'paperroute-device' / 'src' / 'paperroute_device'
    )
    drawing = {
        'paperroute_transit.render.stop_view',
        'paperroute_transit.render.map_view',
        'paperroute_transit.render.chrome',
    }
    banned = {
        kit / 'render' / 'panel.py': {*drawing, STOP_VIEW},
        kit / 'render' / 'notice.py': drawing,
        kit / 'render' / 'sleep.py': drawing,
        TRANSIT / 'core' / 'wifi.py': {STOP_VIEW},
        TRANSIT / 'core' / 'banners.py': {STOP_VIEW},
        TRANSIT / 'core' / 'panel_screens.py': {STOP_VIEW},
        kit / 'adapters' / 'device_page.py': {STOP_VIEW},
    }
    assert all(path.is_file() for path in banned)
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
    """No transit file names TfNSW, NSW, its key scheme or `BASE_URL`."""
    texts = transit_texts()
    offenders = [
        str(path)
        for path, text in texts
        if 'tfnsw' in text.lower()
        or NSW.search(text)
        or KEY_SCHEME.search(text)
    ]
    assert texts
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
        'paperroute_sydney.sydney_feeds',
        'paperroute_transit.core.quota',
        'paperroute_sydney.adapters.tfnsw',
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
        'paperroute_sydney.planner_port',
        'paperroute_transit.adapters.web_pins',
        'paperroute_transit.adapters.web_ways',
        'paperroute_transit.adapters.web_connection',
        'paperroute_sydney.adapters.web_suggest',
    }
    path = TRANSIT / 'adapters' / 'web_display.py'
    names = imported_names(path=path)
    assert not names & banned
    assert 'SYDNEY' not in path.read_text(encoding='utf-8')


def test_board_pages_hold_no_nav_markup() -> None:
    """The board's page files leave their nav to the city."""
    pages = sorted((TRANSIT / 'web').glob('*.html'))
    assert pages
    assert [
        page.name
        for page in pages
        if '<nav' in page.read_text(encoding='utf-8')
    ] == []


def default_built(*, call: ast.Call) -> str | None:
    """
    Name a settings or saved state built without the city's values.

    Parameters
    ----------
    call : ast.Call
        A call.

    Returns
    -------
    str | None
        The class called, when it is `Settings` with no arguments,
        `BoardSettings` without ``peak_windows`` or `Saved` without
        ``settings``; None otherwise.
    """
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(
        func, 'id', None,
    )
    if name not in CITY_DEFAULTS:
        return None
    needed = CITY_DEFAULTS[name]
    given = {keyword.arg for keyword in call.keywords}
    if needed is None:
        return name if not call.args and not call.keywords else None
    return None if needed in given else name


def test_settings_built_with_city_values() -> None:
    """Outside saved.py, settings and saved state carry the city's values."""
    paths = sorted([*APP.rglob('*.py'), *TRANSIT.rglob('*.py')])
    offenders = [
        f'{path.relative_to(ROOT)}:{node.lineno}: {name}'
        for path in paths
        if path != TRANSIT / 'core' / 'saved.py'
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
        if isinstance(node, ast.Call)
        and (name := default_built(call=node)) is not None
    ]
    assert TRANSIT / 'core' / 'saved.py' in paths
    assert not offenders


def app_module(*, path: Path) -> str:
    """
    Give the dotted name of an app source file.

    Parameters
    ----------
    path : Path
        A file under the Sydney app's package.

    Returns
    -------
    str
        The module name, such as ``paperroute_sydney.adapters.tfnsw``;
        a package's ``__init__`` is named for the package.
    """
    parts = path.relative_to(APP.parent).with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def test_engine_imports_no_sydney_module() -> None:
    """Only Sydney's own modules import Sydney's modules."""
    offenders = {
        module: sorted(imported_names(path=path) & SYDNEY_MODULES)
        for path in sorted(APP.rglob('*.py'))
        if (module := app_module(path=path)) not in SYDNEY_MODULES
        and imported_names(path=path) & SYDNEY_MODULES
    }
    assert not offenders


def test_sydney_modules_exist() -> None:
    """Every module named Sydney's is an app module."""
    modules = {app_module(path=path) for path in APP.rglob('*.py')}
    assert SYDNEY_MODULES <= modules


def test_app_holds_only_sydney_modules() -> None:
    """The app's modules are Sydney's and its asset root."""
    modules = {
        app_module(path=path)
        for path in APP.rglob('*.py')
        if path.name != '__init__.py'
    }
    assert modules == SYDNEY_MODULES | {'paperroute_sydney.asset_root'}


def test_testkit_imports_no_app() -> None:
    """No testkit module imports the app, wherever the import sits."""
    paths = sorted(TESTKIT.rglob('*.py'))
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(app)
        for path in paths
        if (
            app := {
                name
                for name in imported_names(path=path)
                if name.split('.')[0] in APPS
            }
        )
    }
    assert paths
    assert 'paperroute_sydney' in APPS
    assert not offenders


def test_imported_names_see_package_from_imports(tmp_path: Path) -> None:
    """Importing a module from its package names the module."""
    path = tmp_path / 'engine.py'
    path.write_text(
        'from paperroute_sydney import city\n'
        'from paperroute_sydney import sydney_feeds as feeds\n',
        encoding='utf-8',
    )
    assert {'paperroute_sydney.city', 'paperroute_sydney.sydney_feeds'} <= (
        imported_names(path=path)
    )


def defined_names(*, path: Path) -> list[str]:
    """
    List the names a module defines at its top level.

    Parameters
    ----------
    path : Path
        The source file.

    Returns
    -------
    list[str]
        Each function, class and assigned name, in order.
    """
    names: list[str] = []
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        ):
            names.append(node.name)
        elif isinstance(node, ast.AnnAssign | ast.Assign):
            targets = (
                [node.target] if isinstance(node, ast.AnnAssign)
                else node.targets
            )
            names += [
                target.id
                for target in targets
                if isinstance(target, ast.Name)
            ]
    return names


def test_app_main_is_thin() -> None:
    """The app's entry defines only Sydney's parts and main."""
    assert defined_names(path=APP / '__main__.py') == [
        'tfnsw_feeds', 'tfnsw_keys', 'SYDNEY_PARTS', 'main',
    ]
