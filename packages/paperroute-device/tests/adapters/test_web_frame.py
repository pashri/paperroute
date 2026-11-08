"""Tests for reading the web shell's own files and drawing its nav."""

import subprocess
import sys
from pathlib import Path
from typing import Final

from paperroute_device.adapters.web_frame import (
    DEVICE_NAV,
    LIVE_NAV,
    SHELL_WEB,
    NavItem,
    nav_html,
    page_frame,
)
from paperroute_testkit.paths import ROOT

HIGHER: Final[frozenset[str]] = frozenset({
    'paperroute_transit',
}) | frozenset(
    {
        path.parent.name
        for path in ROOT.glob('apps/*/src/*/__init__.py')
    },
)
READ_SHELL: Final[str] = (
    'import sys\n'
    'from paperroute_device.adapters.web_frame import shell_bytes, '
    'shell_text\n'
    "page = shell_text(name='device.html')\n"
    "font = shell_bytes(name='fonts/public-sans.woff2')\n"
    'print(len(page), len(font))\n'
    'print(*sorted(sys.modules))\n'
)


def test_shell_web_is_in_the_kit() -> None:
    """The shell's files live in the device kit's own package."""
    assert Path(str(SHELL_WEB)) == (
        ROOT / 'packages/paperroute-device/src/paperroute_device/web'
    )


def test_shell_files_read_without_app() -> None:
    """Shell files read with no asset root and no app or transit code."""
    result = subprocess.run(
        [sys.executable, '-c', READ_SHELL],
        check=True,
        capture_output=True,
        text=True,
    )
    sizes, modules = result.stdout.splitlines()
    assert all(int(size) > 0 for size in sizes.split())
    offenders = [
        name
        for name in modules.split()
        if name.split('.')[0] in HIGHER
    ]
    assert not offenders


def test_nav_html_marks_only_current() -> None:
    """Only the open page's link is marked; None marks no link."""
    items = (LIVE_NAV, DEVICE_NAV)
    marked = nav_html(items=items, current='/device')
    assert marked.count('aria-current="page"') == 1
    assert '<a href="/device" aria-current="page">' in marked
    assert '<a href="/">' in marked
    assert 'aria-current' not in nav_html(items=items, current=None)


def test_nav_html_escapes_label() -> None:
    """A label is drawn as text, never as markup."""
    item = NavItem(href='/ab', label='A&B', icon='<path d="M0 0"/>')
    nav = nav_html(items=(item,), current=None)
    assert '<span>A&amp;B</span>' in nav
    assert 'A&B' not in nav


def test_kit_never_names_board_css() -> None:
    """No kit web file names the app's board stylesheet."""
    pending = [SHELL_WEB]
    found: list[str] = []
    while pending:
        for entry in pending.pop().iterdir():
            if entry.is_dir():
                pending.append(entry)
            elif b'board.css' in entry.read_bytes():
                found.append(entry.name)
    assert not found


def test_sheets_mark_filled_or_dropped() -> None:
    """No sheets leave no line; a sheet links straight after live.css."""
    live = '  <link rel="stylesheet" href="/live.css">\n'
    bare = page_frame(name='device.html', nav=(), current=None)
    assert '{{sheets}}' not in bare
    assert live + '  <script' in bare
    linked = page_frame(
        name='device.html', nav=(), current=None, sheets=('a.css',),
    )
    assert live + '  <link rel="stylesheet" href="/a.css">\n' in linked
    assert '{{sheets}}' not in linked


def test_higher_includes_apps() -> None:
    """The higher packages include every app."""
    assert 'paperroute_sydney' in HIGHER
