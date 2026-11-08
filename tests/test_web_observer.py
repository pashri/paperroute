"""Tests for the web app's observers."""

import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from paperroute_transit.observers import Timings
from paperroute_transit.summary import Summary
from paperroute_transit.web_observer import web_observer


@pytest.fixture(name='sock_dir')
def _sock_dir() -> Iterator[Path]:
    """A short directory, as macOS caps AF_UNIX paths at 104 bytes."""
    path = Path(tempfile.mkdtemp(dir='/tmp'))
    yield path
    shutil.rmtree(path)


def test_web_observer_pings_watchdog_and_led(
    sock_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tick reaches systemd and the first frame sets the LED."""
    path = sock_dir / 'n.sock'
    (tmp_path / 'trigger').write_text('mmc0\n')
    monkeypatch.setenv('NOTIFY_SOCKET', str(path))
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(path))
        server.settimeout(1)
        observer = web_observer(
            timings=Timings(),
            summary=Summary(),
            led_root=tmp_path,
        )
        observer.ticked()
        got = [server.recv(64), server.recv(64)]
    assert got == [b'READY=1', b'WATCHDOG=1']
    assert (tmp_path / 'trigger').read_text() == 'mmc0\n'
    observer.frame_drawn(render_ms=1.0)
    assert (tmp_path / 'trigger').read_text() == 'default-on'
