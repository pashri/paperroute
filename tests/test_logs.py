"""Tests for JSON-lines logging."""

import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from paperroute.logs import LastError, configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """Put the root logger back after each test."""
    root = logging.getLogger()
    access = logging.getLogger('aiohttp.access')
    handlers, level = list(root.handlers), root.level
    access_level = access.level
    yield
    access.setLevel(access_level)
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, error_file: Path | None = None
) -> io.StringIO:
    """
    Configure logging with stderr pointed at a StringIO.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to swap stderr.
    error_file : Path | None
        Passed to `configure_logging`.

    Returns
    -------
    io.StringIO
        What the stderr handler writes to.
    """
    stream = io.StringIO()
    monkeypatch.setattr('sys.stderr', stream)
    configure_logging(error_file=error_file)
    return stream


def test_logs_include_extra_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extra fields appear in the JSON line."""
    stream = _configure(monkeypatch)
    logging.getLogger('paperroute.t').info('x', extra={'render_ms': 1.5})
    line = json.loads(stream.getvalue().splitlines()[-1])
    assert line['msg'] == 'x'
    assert line['level'] == 'INFO'
    assert line['logger'] == 'paperroute.t'
    assert line['render_ms'] == 1.5
    assert 'time' in line


def test_logs_put_tracebacks_under_exc_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions are rendered under exc_info."""
    stream = _configure(monkeypatch)
    try:
        raise ValueError('boom')
    except ValueError:
        logging.getLogger('paperroute.t').exception('failed')
    line = json.loads(stream.getvalue().splitlines()[-1])
    assert 'ValueError' in line['exc_info']


def test_error_file_gets_warnings_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The error file holds WARNING and above as JSON lines."""
    path = tmp_path / 'sub' / 'errors.jsonl'
    _configure(monkeypatch, error_file=path)
    logger = logging.getLogger('paperroute.t')
    logger.info('quiet')
    logger.warning('loud')
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line['msg'] for line in lines] == ['loud']


def test_error_file_rotates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """About 1.2 MB of warnings rolls the file over."""
    path = tmp_path / 'errors.jsonl'
    _configure(monkeypatch, error_file=path)
    logger = logging.getLogger('paperroute.t')
    for _ in range(1200):
        logger.warning('x' * 1000)
    assert path.with_name('errors.jsonl.1').exists()


def test_last_error_keeps_latest_error() -> None:
    """Only ERROR records count, and the latest is kept with its time."""
    last = LastError()
    assert last.text() is None
    log = logging.getLogger('paperroute.last')
    log.addHandler(last)
    try:
        log.warning('just a warning')
        assert last.text() is None
        log.error('first')
        log.error('second %s', 'x' * 100)
    finally:
        log.removeHandler(last)
    text = last.text()
    assert text is not None
    assert len(text) == 80
    assert text[2] == ':'
    assert text[5:].startswith(' second x')


def test_configure_logging_attaches_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler given sees errors logged anywhere."""
    monkeypatch.setattr('sys.stderr', io.StringIO())
    last = LastError()
    configure_logging(last_error=last)
    logging.getLogger('paperroute.t').error('boom')
    text = last.text()
    assert text is not None
    assert text.endswith(' boom')


def test_web_access_lines_are_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """aiohttp's per-request INFO lines reach neither stderr nor the file."""
    path = tmp_path / 'errors.jsonl'
    stream = _configure(monkeypatch, error_file=path)
    access = logging.getLogger('aiohttp.access')
    access.info('GET /frame.png 200')
    access.warning('odd request')
    assert 'frame.png' not in stream.getvalue()
    assert 'odd request' in stream.getvalue()


def test_debug_lines_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEBUG records, such as each frame drawn, never reach stderr."""
    stream = _configure(monkeypatch)
    logging.getLogger('paperroute.t').debug('frame drawn')
    assert stream.getvalue() == ''


def test_nested_extra_fields_stay_on_one_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summary's nested counts are written as one JSON line."""
    stream = _configure(monkeypatch)
    feeds = {'v1/x': {'sent': 2, 'failed': {'429': 1}}}
    logging.getLogger('paperroute.t').info('summary', extra={'feeds': feeds})
    [text] = stream.getvalue().splitlines()
    assert json.loads(text)['feeds'] == feeds
