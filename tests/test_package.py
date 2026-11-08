"""Tests for the package marker."""

from pathlib import Path

import paperroute_sydney
from paperroute_testkit.paths import ROOT


def test_version_is_set() -> None:
    """The package exposes its version."""
    assert paperroute_sydney.__version__ == '0.1.0'


def test_app_comes_from_apps() -> None:
    """The Sydney app is imported from its directory under ``apps``."""
    assert Path(str(paperroute_sydney.__file__)).is_relative_to(
        ROOT / 'apps' / 'paperroute-sydney' / 'src',
    )
