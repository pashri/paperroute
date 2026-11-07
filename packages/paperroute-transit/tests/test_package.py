"""Tests for where the package is installed from."""

from pathlib import Path

import paperroute_transit
from paperroute_testkit.paths import ROOT


def test_package_comes_from_workspace() -> None:
    """The package imports from its workspace source tree."""
    assert Path(paperroute_transit.__file__).is_relative_to(
        ROOT / 'packages' / 'paperroute-transit' / 'src',
    )
