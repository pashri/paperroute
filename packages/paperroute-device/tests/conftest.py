"""Give the device kit's tests the test kit's web files."""

import pytest

from paperroute_device.assets import use_assets
from paperroute_testkit.fake_assets import TEST_ASSETS


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the asset root before any test runs.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    use_assets(root=TEST_ASSETS)
