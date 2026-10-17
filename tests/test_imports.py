"""Tests for what the device's code imports."""

import subprocess
import sys
from typing import Final

DEVICE_IMPORTS: Final[str] = (
    'import paperroute.__main__, paperroute.app, sys; '
    "assert not {'osmium', 'shapely'} & set(sys.modules)"
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
