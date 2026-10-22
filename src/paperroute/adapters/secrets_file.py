"""The file holding the TfNSW API key, read again whenever it changes."""

import re
from pathlib import Path
from typing import Final

from paperroute.adapters.state_file import write_atomic
from paperroute.adapters.tfnsw import key_path_from_env

__all__ = ['KEY_PATTERN', 'SecretsFile', 'key_path_from_env']

KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r'[\x21-\x7e]{16,4096}')
SECRETS_MODE: Final[int] = 0o600
SECRETS_PREFIX: Final[str] = '.secrets.'


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
            The key, stripped; None when the file is missing or blank.
        """
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self.seen, self.key = None, None
            return None
        seen = (stat.st_mtime_ns, stat.st_size)
        if seen != self.seen:
            text = self.path.read_text(encoding='utf-8').strip()
            self.seen, self.key = seen, text or None
        return self.key

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
