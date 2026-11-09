"""Give the Sydney app's tests Sydney as the city under test."""

import pytest

from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.city_setting import use_city_under_test


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the city under test and its asset root before any test runs.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    use_city_under_test(city=SYDNEY, assets=APP_ASSETS)
