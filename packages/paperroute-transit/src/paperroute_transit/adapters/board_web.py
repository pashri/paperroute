"""The board's web files: the app's own copy first, then transit's."""

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

from paperroute_device.assets import assets

BOARD_WEB: Final[Traversable] = files('paperroute_transit') / 'web'


def board_path(*, name: str) -> Traversable:
    """
    Find one of the board's web files.

    Parameters
    ----------
    name : str
        Path within a web directory, such as `vendor/tom-select.css`.

    Returns
    -------
    Traversable
        The file in the app's web directory when it has one, else the
        file in transit's.

    Raises
    ------
    FileNotFoundError
        If neither web directory holds the file.
    """
    parts = name.split('/')
    for root in (assets().web, BOARD_WEB):
        path = root.joinpath(*parts)
        if path.is_file():
            return path
    raise FileNotFoundError(name)


def board_text(*, name: str) -> str:
    """
    Read one of the board's web files as text.

    Parameters
    ----------
    name : str
        Path within a web directory.

    Returns
    -------
    str
        The file contents, from the app's copy when it has one.

    Raises
    ------
    FileNotFoundError
        If neither web directory holds the file.
    """
    return board_path(name=name).read_text(encoding='utf-8')


def board_bytes(*, name: str) -> bytes:
    """
    Read one of the board's web files as bytes.

    Parameters
    ----------
    name : str
        Path within a web directory, such as `vendor/tom-select.css`.

    Returns
    -------
    bytes
        The file contents, from the app's copy when it has one.

    Raises
    ------
    FileNotFoundError
        If neither web directory holds the file.
    """
    return board_path(name=name).read_bytes()
