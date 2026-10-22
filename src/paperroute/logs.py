"""JSON-lines logging."""

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Final

from pythonjsonlogger.json import JsonFormatter

from paperroute.core.model import SYDNEY

FORMAT: Final[str] = '{asctime}{levelname}{name}{message}'
FIELD_NAMES: Final[dict[str, str]] = {
    'asctime': 'time',
    'levelname': 'level',
    'name': 'logger',
    'message': 'msg',
}
ERROR_FILE_BYTES: Final[int] = 1_000_000
ERROR_FILE_BACKUPS: Final[int] = 3
LAST_ERROR_CHARS: Final[int] = 80


class LastError(logging.Handler):
    """Keeps the latest error logged, with its Sydney time."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._text: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        """
        Keep a record as the latest error.

        Parameters
        ----------
        record : logging.LogRecord
            The record.
        """
        moment = datetime.fromtimestamp(record.created, tz=SYDNEY)
        text = f'{moment:%H:%M} {record.getMessage()}'
        self._text = text[:LAST_ERROR_CHARS]

    def text(self) -> str | None:
        """
        Give the latest error.

        Returns
        -------
        str | None
            'hh:mm message', cut to `LAST_ERROR_CHARS` characters; None
            before the first error.
        """
        return self._text


def _formatter() -> JsonFormatter:
    """
    Build the JSON formatter.

    Returns
    -------
    JsonFormatter
        Formatter that renames the standard fields.
    """
    return JsonFormatter(
        FORMAT,
        style='{',
        rename_fields=FIELD_NAMES,
    )


def configure_logging(
    *,
    error_file: Path | None = None,
    last_error: LastError | None = None,
) -> None:
    """
    Send INFO and above to stderr as JSON lines.

    Parameters
    ----------
    error_file : Path | None
        If set, WARNING and above are also written here, rotating at
        about 1 MB with three backups.
    last_error : LastError | None
        If set, it is given every ERROR record too.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if error_file is not None:
        error_file.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            error_file,
            maxBytes=ERROR_FILE_BYTES,
            backupCount=ERROR_FILE_BACKUPS,
        )
        rotating.setLevel(logging.WARNING)
        handlers.append(rotating)
    for handler in handlers:
        handler.setFormatter(_formatter())
    if last_error is not None:
        handlers.append(last_error)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
