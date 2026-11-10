"""Give the transit kit's tests the made-up test city."""

import pytest

from paperroute_device_testkit.fake_assets import TEST_ASSETS
from paperroute_testkit.city_setting import use_city_under_test
from paperroute_testkit.fake_city import TEST_CITY


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the city under test and its asset root before any test runs.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    use_city_under_test(city=TEST_CITY, assets=TEST_ASSETS)
