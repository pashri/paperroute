"""The file holding an API key, read again whenever it changes."""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Final

from paperroute_device.adapters.atomic_file import write_atomic

KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r'[\x21-\x7e]{16,4096}')
SECRETS_MODE: Final[int] = 0o600
SECRETS_PREFIX: Final[str] = '.secrets.'
KEY_FILE_VARIABLE: Final[str] = 'PAPERROUTE_KEY_FILE_{name}'

logger = logging.getLogger(__name__)


def key_path_from_env(*, name: str) -> Path:
    """
    Name a key's file from its environment variable.

    The variable is `PAPERROUTE_KEY_FILE_` and the key's name in upper
    case. Exits with a message when it is unset.

    Parameters
    ----------
    name : str
        The key's name.

    Returns
    -------
    Path
        The key file, which may not exist yet.
    """
    variable = KEY_FILE_VARIABLE.format(name=name.upper())
    key_file = os.getenv(variable)
    if not key_file:
        sys.exit(f'Set {variable} to the file holding the {name} key.')
    return Path(key_file).expanduser()


class SecretsFile:
    """
    The API key file; the key is never logged or shown in a repr.

    Parameters
    ----------
    path : Path
        The file. A missing or blank file means no key.
    """

    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.seen: tuple[int, int] | None = None
        self.key: str | None = None

    def read(self) -> str | None:
        """
        Read the key, opening the file again only when it has changed.

        Returns
        -------
        str | None
            The key, stripped; None when the file is missing, blank or
            unreadable.
        """
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self.seen, self.key = None, None
            return None
        seen = (stat.st_mtime_ns, stat.st_size)
        if seen != self.seen:
            self.seen, self.key = seen, self.load()
        return self.key

    def load(self) -> str | None:
        """
        Read the file; a failure is logged by file name only.

        Returns
        -------
        str | None
            The key, or None when blank or unreadable.
        """
        try:
            text = self.path.read_text(encoding='utf-8').strip()
        except (OSError, UnicodeDecodeError):
            logger.warning('%s unreadable, treating as no key', self.path)
            return None
        return text or None

    def present(self) -> bool:
        """
        Say whether a key is set.

        Returns
        -------
        bool
            True when the file holds a key.
        """
        return self.read() is not None

    def write(self, *, key: str) -> None:
        """
        Store a key, readable only by the file's owner.

        Parameters
        ----------
        key : str
            The key; surrounding whitespace is dropped.

        Raises
        ------
        ValueError
            When the key is not 16 to 4096 printable characters with no
            spaces. The message never holds the key.
        """
        key = key.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError('not a valid API key')
        self.save(text=key)

    def clear(self) -> None:
        """Remove the key, leaving an empty private file."""
        self.save(text='')

    def save(self, *, text: str) -> None:
        """
        Replace the file's contents.

        Parameters
        ----------
        text : str
            New contents.
        """
        write_atomic(
            path=self.path,
            text=text,
            mode=SECRETS_MODE,
            prefix=SECRETS_PREFIX,
        )
