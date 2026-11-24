"""Give the repository's own tests Sydney as the city under test."""

import logging
from collections.abc import Iterator

import pytest

from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.city_setting import use_city_under_test

use_city_under_test(city=SYDNEY, assets=APP_ASSETS)


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """
    Put the root logger back after a test that configures it.

    Yields
    ------
    None
        Control to the test.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
