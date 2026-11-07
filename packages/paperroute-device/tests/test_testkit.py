"""Tests for the test kit's device fakes."""

import subprocess
import sys
from typing import Final

import pytest

HIGHER: Final[frozenset[str]] = frozenset({'paperroute', 'paperroute_transit'})


@pytest.mark.parametrize('module', [
    'paperroute_testkit.device_fakes',
    'paperroute_testkit.device_pages',
])
def test_device_fakes_load_no_higher_packages(module: str) -> None:
    """The device fakes load without the app or transit code."""
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            f'import {module}, sys; print(*sorted(sys.modules))',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    offenders = [
        name
        for name in result.stdout.split()
        if name.split('.')[0] in HIGHER
    ]
    assert not offenders
