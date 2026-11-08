"""The city and asset root the test kit builds its values for."""

from typing import Final

from paperroute_device.assets import AssetRoot, use_assets
from paperroute_transit.core.city import City

IN_USE: Final[list[City]] = []


def use_city_under_test(*, city: City, assets: AssetRoot) -> None:
    """
    Set the city under test and its asset root for the whole run.

    Neither can change once set.

    Parameters
    ----------
    city : City
        The city the test kit's values belong to.
    assets : AssetRoot
        The app's asset directories.

    Raises
    ------
    ValueError
        When a different city is already under test.
    """
    if IN_USE and IN_USE[0] != city:
        raise ValueError('a different city is already under test')
    use_assets(root=assets)
    IN_USE[:] = [city]


def city_under_test() -> City:
    """
    Give the city under test.

    Returns
    -------
    City
        The city the root ``conftest.py`` set.

    Raises
    ------
    RuntimeError
        When no city is set yet.
    """
    if not IN_USE:
        raise RuntimeError(
            'no city under test: the root conftest.py must call '
            'use_city_under_test first',
        )
    return IN_USE[0]
