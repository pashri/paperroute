"""The web shell's own files, shipped with the device kit."""

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

SHELL_WEB: Final[Traversable] = files('paperroute_device') / 'web'


def shell_path(*, name: str) -> Traversable:
    """
    Find one of the shell's files.

    Parameters
    ----------
    name : str
        Path within the shell's web directory, such as
        `fonts/public-sans.woff2`.

    Returns
    -------
    Traversable
        The file.
    """
    return SHELL_WEB.joinpath(*name.split('/'))


def shell_text(*, name: str) -> str:
    """
    Read one of the shell's files as text.

    Parameters
    ----------
    name : str
        Path within the shell's web directory.

    Returns
    -------
    str
        The file contents.
    """
    return shell_path(name=name).read_text(encoding='utf-8')


def shell_bytes(*, name: str) -> bytes:
    """
    Read one of the shell's files as bytes.

    Parameters
    ----------
    name : str
        Path within the shell's web directory, such as
        `fonts/public-sans.woff2`.

    Returns
    -------
    bytes
        The file contents.
    """
    return shell_path(name=name).read_bytes()
