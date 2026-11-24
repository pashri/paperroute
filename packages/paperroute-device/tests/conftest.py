"""Give the device kit's tests the test kit's web files."""

import logging
from collections.abc import Iterator

import pytest

from paperroute_device.assets import use_assets
from paperroute_device_testkit.fake_assets import TEST_ASSETS


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
    """Put the root and access loggers back after a test that sets them."""
    root = logging.getLogger()
    access = logging.getLogger('aiohttp.access')
    handlers, level = list(root.handlers), root.level
    access_level = access.level
    yield
    access.setLevel(access_level)
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
