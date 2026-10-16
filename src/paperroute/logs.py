"""JSON-lines logging."""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Final

from pythonjsonlogger.json import JsonFormatter

FORMAT: Final[str] = '{asctime}{levelname}{name}{message}'
FIELD_NAMES: Final[dict[str, str]] = {
    'asctime': 'time',
    'levelname': 'level',
    'name': 'logger',
    'message': 'msg',
}
ERROR_FILE_BYTES: Final[int] = 1_000_000
ERROR_FILE_BACKUPS: Final[int] = 3


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


def configure_logging(*, error_file: Path | None = None) -> None:
    """
    Send INFO and above to stderr as JSON lines.

    Parameters
    ----------
    error_file : Path | None
        If set, WARNING and above are also written here, rotating at
        about 1 MB with three backups.
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
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
