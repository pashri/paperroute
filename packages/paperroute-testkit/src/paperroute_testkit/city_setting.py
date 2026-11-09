"""The city and asset root the test kit builds its values for."""

from dataclasses import dataclass
from typing import Final

from paperroute_device.assets import AssetRoot, use_assets
from paperroute_transit.core.city import City


@dataclass
class CitySetting:
    """
    The city under test and its asset root, once set.

    Attributes
    ----------
    city : City | None
        The city the test kit's values belong to; None until set.
    assets : AssetRoot | None
        The app's asset directories; None until set.
    """

    city: City | None = None
    assets: AssetRoot | None = None


UNDER_TEST: Final[CitySetting] = CitySetting()


def use_city_under_test(*, city: City, assets: AssetRoot) -> None:
    """
    Set the city under test and its asset root for the whole run.

    Neither can change once set. Setting the same city and root again
    changes nothing.

    Parameters
    ----------
    city : City
        The city the test kit's values belong to.
    assets : AssetRoot
        The app's asset directories.

    Raises
    ------
    ValueError
        When a different city, or a different asset root, is already
        under test.
    """
    if UNDER_TEST.city is not None and UNDER_TEST.city != city:
        raise ValueError('a different city is already under test')
    if UNDER_TEST.assets is not None and UNDER_TEST.assets != assets:
        raise ValueError('a different asset root is already under test')
    use_assets(root=assets)
    UNDER_TEST.city = city
    UNDER_TEST.assets = assets


def city_under_test() -> City:
    """
    Give the city under test.

    Returns
    -------
    City
        The city a ``conftest.py`` set.

    Raises
    ------
    RuntimeError
        When no city is set yet.
    """
    if UNDER_TEST.city is None:
        raise RuntimeError(
            'no city under test: a conftest.py must call '
            'use_city_under_test first',
        )
    return UNDER_TEST.city


def line_mode() -> str:
    """
    Give the first mode of the city under test chosen by line.

    Returns
    -------
    str
        Its mode id, such as Sydney's 'train'.

    Raises
    ------
    ValueError
        When the city has no such mode.
    """
    for spec in city_under_test().modes:
        if spec.by_line:
            return spec.id
    raise ValueError('the city under test has no mode chosen by line')


def street_mode() -> str:
    """
    Give the first mode of the city under test with place tags.

    Returns
    -------
    str
        Its mode id, such as Sydney's 'bus'.

    Raises
    ------
    ValueError
        When the city has no such mode.
    """
    for spec in city_under_test().modes:
        if spec.tagged:
            return spec.id
    raise ValueError('the city under test has no mode with place tags')


def source_of(*, mode: str) -> str:
    """
    Give the first feed source of one mode of the city under test.

    Parameters
    ----------
    mode : str
        Mode id.

    Returns
    -------
    str
        Its alphabetically first feed source.

    Raises
    ------
    ValueError
        When the mode has no feed source.
    """
    sources = city_under_test().feeds.sources_of(mode=mode)
    if not sources:
        raise ValueError(f'mode {mode!r} has no feed source')
    return min(sources)
