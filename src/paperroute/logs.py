"""JSON-lines logging."""

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """One JSON object per record."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Render a record as JSON.

        Parameters
        ----------
        record : logging.LogRecord
            Record.

        Returns
        -------
        str
            One line of JSON.
        """
        line = {
            'time': self.formatTime(record),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        if record.exc_info:
            line['exc'] = self.formatException(record.exc_info)
        return json.dumps(line)


def configure_logging() -> None:
    """Send INFO and above to stderr as JSON lines."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
