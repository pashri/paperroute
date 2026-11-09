"""Tests for the setting that gives the test kit its city."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from paperroute_device.assets import AssetRoot, assets
from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.city_setting import (
    city_under_test,
    use_city_under_test,
)

UNSET_RUN: Final[str] = (
    'from paperroute_testkit.city_setting import city_under_test\n'
    'try:\n'
    '    city_under_test()\n'
    'except RuntimeError as error:\n'
    '    print(error)\n'
)


def test_city_under_test_needs_setting() -> None:
    """Outside pytest, with no setting, asking for the city fails."""
    result = subprocess.run(
        [sys.executable, '-c', UNSET_RUN],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == (
        'no city under test: a conftest.py must call '
        'use_city_under_test first\n'
    )


def test_city_under_test_is_sydney() -> None:
    """The root tests' conftest fills the setting with Sydney."""
    assert city_under_test() is SYDNEY


def test_same_city_accepted() -> None:
    """Setting the city in use again changes nothing."""
    use_city_under_test(city=SYDNEY, assets=APP_ASSETS)
    assert city_under_test() is SYDNEY


def test_second_city_refused() -> None:
    """Once Sydney is under test, another city cannot replace it."""
    with pytest.raises(ValueError, match='different city'):
        use_city_under_test(
            city=replace(SYDNEY, name='Other'),
            assets=APP_ASSETS,
        )
    assert city_under_test() is SYDNEY


def test_second_asset_root_refused(tmp_path: Path) -> None:
    """The same city with another asset root is refused; nothing moves."""
    with pytest.raises(ValueError, match='different asset root'):
        use_city_under_test(city=SYDNEY, assets=AssetRoot(web=tmp_path))
    assert city_under_test() is SYDNEY
    assert assets() == APP_ASSETS
