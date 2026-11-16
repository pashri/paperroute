"""Give the Cambridge app's tests Cambridge as the city under test."""

from collections.abc import Iterator

import pytest

from paperroute_cambridge.asset_root import APP_ASSETS
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_testkit.city_setting import use_city_under_test
from tests.build.timetable_rows import OPENED


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the city under test and its asset root before any test runs.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    use_city_under_test(city=CAMBRIDGE, assets=APP_ASSETS)


@pytest.fixture(autouse=True)
def _close_connections() -> Iterator[None]:
    """
    Close and forget each connection the test builders opened.

    Yields
    ------
    None
        Once, while the test runs.
    """
    yield
    while OPENED:
        OPENED.pop().close()
