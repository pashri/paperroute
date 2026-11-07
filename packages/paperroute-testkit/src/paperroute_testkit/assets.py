"""A pytest plugin that gives every test run the app's assets."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the app's asset root before any test runs.

    The app is imported here, not at the top, so that coverage, which
    starts after plugins load, measures it.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    # pylint: disable-next=import-outside-toplevel
    from paperroute.asset_root import use_app_assets
    use_app_assets()
