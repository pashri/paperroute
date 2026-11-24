"""Tests for the positions saving script."""

import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType

import pytest

from tests.helpers import load_module

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'save_positions.py'
BODY = b'feed-bytes'
KEY = 'secret-key-123'
ACCEPT = 'application/x-google-protobuf'


class Handler(BaseHTTPRequestHandler):
    """Serves a body only to a request carrying the key."""

    def answer_get(self) -> None:
        """
        Answer a GET with the body, 403 without the key, or 406 like BODS.

        TfNSW needs the protobuf Accept header; BODS refuses it.
        """
        tfnsw = self.headers.get('Authorization') == f'apikey {KEY}'
        bods = f'api_key={KEY}' in self.path
        accept = self.headers.get('Accept')
        if bods and accept == ACCEPT:
            status = HTTPStatus.NOT_ACCEPTABLE
        elif (tfnsw and accept == ACCEPT) or bods:
            status = HTTPStatus.OK
        else:
            status = HTTPStatus.FORBIDDEN
        self.send_response(status)
        self.end_headers()
        self.wfile.write(BODY if status == HTTPStatus.OK else b'')

    def log_message(self, format: str, *args: object) -> None:
        """Stay quiet."""


setattr(Handler, 'do_GET', Handler.answer_get)


def load_script() -> ModuleType:
    """
    Import the script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    return load_module(name='save_positions', path=SCRIPT)


@pytest.mark.parametrize('source', ['tfnsw', 'bods'])
def test_save_positions_writes_the_body_and_hides_the_key(
    source: str, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Each source's feed is saved with the key sent, and not printed."""
    key_file = tmp_path / 'key'
    key_file.write_text(KEY + '\n', encoding='utf-8')
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status = load_script().main(
            argv=[
                '--source', source,
                '--out', str(tmp_path / 'p.pb'),
                '--key-file', str(key_file),
                '--base-url', f'http://127.0.0.1:{server.server_port}/',
            ],
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert status == 0
    assert (tmp_path / 'p.pb').read_bytes() == BODY
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


def test_save_positions_missing_key_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing key file exits 1."""
    status = load_script().main(
        argv=['--out', str(tmp_path / 'p.pb'), '--key-file', str(tmp_path)],
    )
    assert status == 1
    assert 'no tfnsw key found' in capsys.readouterr().err
