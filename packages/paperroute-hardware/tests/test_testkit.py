"""Tests for the test kit's hardware fakes."""

import subprocess
import sys
from typing import Final

FAKES_IMPORTS: Final[str] = (
    'import paperroute_testkit.hardware_fakes, sys; '
    'print(*sorted(sys.modules))'
)
HIGHER: Final[frozenset[str]] = frozenset(
    {'paperroute', 'paperroute_device', 'paperroute_transit'},
)


def test_hardware_fakes_load_no_higher_packages() -> None:
    """The hardware fakes load without the app, device or transit code."""
    result = subprocess.run(
        [sys.executable, '-c', FAKES_IMPORTS],
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
