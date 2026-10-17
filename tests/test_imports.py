"""Tests for what the device's code imports."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

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
