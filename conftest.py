"""Give every test run in the repository Sydney as the city under test."""

import pytest

from paperroute_testkit.city_setting import use_city_under_test


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the city under test and its asset root before any test runs.

    The app is imported here, not at the top, so that coverage, which
    starts after this file loads, measures it.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    # pylint: disable=import-outside-toplevel
    from paperroute_sydney.asset_root import APP_ASSETS
    from paperroute_sydney.city import SYDNEY

    # pylint: enable=import-outside-toplevel
    use_city_under_test(city=SYDNEY, assets=APP_ASSETS)
