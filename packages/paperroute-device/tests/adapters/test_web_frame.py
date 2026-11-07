"""Tests for reading the web shell's own files."""

import subprocess
import sys
from pathlib import Path
from typing import Final

from paperroute_device.adapters.web_frame import SHELL_WEB
from paperroute_testkit.paths import ROOT

HIGHER: Final[frozenset[str]] = frozenset({'paperroute', 'paperroute_transit'})
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
