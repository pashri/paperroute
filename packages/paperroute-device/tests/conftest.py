"""Give the device kit's tests the test kit's web files."""

from collections.abc import Iterator

import pytest

from paperroute_device.assets import use_assets
from paperroute_device_testkit.fake_assets import TEST_ASSETS
from paperroute_device_testkit.logs import kept_logging


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


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """
    Put the root and access loggers back after a test that sets them.

    Yields
    ------
    None
        Control to the test.
    """
    with kept_logging():
        yield
