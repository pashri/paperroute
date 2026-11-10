"""Tests for where the package is installed from."""

from pathlib import Path

import paperroute_device
from paperroute_device_testkit.paths import ROOT


def test_package_comes_from_workspace() -> None:
    """The package imports from its workspace source tree."""
    assert Path(paperroute_device.__file__).is_relative_to(
        ROOT / 'packages' / 'paperroute-device' / 'src',
    )
