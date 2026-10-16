"""Tests for the package marker."""

import paperroute


def test_version_is_set() -> None:
    """The package exposes its version."""
    assert paperroute.__version__ == '0.1.0'
