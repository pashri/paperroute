"""Where the fonts and web files are, as the app sets it."""

from dataclasses import dataclass
from importlib.resources.abc import Traversable
from typing import Final


@dataclass(frozen=True)
class AssetRoot:
    """
    The directories holding the app's package data.

    Attributes
    ----------
    fonts : Traversable
        Holds the Overpass ``.ttf`` files.
    web : Traversable
        Holds the app's own web files.
    """

    fonts: Traversable
    web: Traversable


IN_USE: Final[list[AssetRoot]] = []


def use_assets(*, root: AssetRoot) -> None:
    """
    Set the asset root for the rest of the process.

    Fonts are cached once loaded, so the root cannot change later.

    Parameters
    ----------
    root : AssetRoot
        The app's asset directories.

    Raises
    ------
    ValueError
        When a different root is already in use.
    """
    if IN_USE and IN_USE[0] != root:
        raise ValueError('a different asset root is already in use')
    IN_USE[:] = [root]


def assets() -> AssetRoot:
    """
    Give the asset root in use.

    Returns
    -------
    AssetRoot
        The root the app set.

    Raises
    ------
    RuntimeError
        When no root is set yet.
    """
    if not IN_USE:
        raise RuntimeError('no asset root: the app must call use_assets first')
    return IN_USE[0]
