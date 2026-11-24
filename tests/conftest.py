"""Give the repository's own tests Sydney as the city under test."""

from collections.abc import Iterator

import pytest

from paperroute_device_testkit.logs import kept_logging
from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.city_setting import use_city_under_test

use_city_under_test(city=SYDNEY, assets=APP_ASSETS)


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
