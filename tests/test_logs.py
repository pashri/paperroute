"""Tests for JSON-lines logging."""

import json
import logging

from paperroute.logs import JsonFormatter


def test_json_formatter_writes_one_object() -> None:
    """Records become one JSON object with level, logger and message."""
    record = logging.LogRecord(
        'paperroute.app',
        logging.WARNING,
        __file__,
        1,
        'feed %s failed',
        ('buses',),
        None,
    )
    line = json.loads(JsonFormatter().format(record))
    assert line['level'] == 'WARNING'
    assert line['logger'] == 'paperroute.app'
    assert line['msg'] == 'feed buses failed'
